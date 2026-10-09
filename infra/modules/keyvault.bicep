// WHAT IT IS
//   Azure Key Vault: the one place this deployment's secrets live. Each app
//   reads only through its own managed identity; the overlay (config/azure.yaml)
//   names secrets as {key_vault: name} and never holds a value.
//
// WHICH CONCERN IT SERVES
//   Secrets (stack binding `secrets: key-vault`; FINDINGS F-33). Access is by
//   Azure RBAC, not access policies: "Key Vault Secrets User" (read) for each
//   app's identity and for APIM (the Groq key and the named values), "Key Vault
//   Secrets Officer" (write) for the owner who runs the hooks.
//
//   The Groq key is never in a file or a parameter: infra/hooks/postprovision.sh
//   prompts for it and runs `az keyvault secret set`. Until then this module
//   writes a placeholder (tagged placeholder=true) so APIM's named value can be
//   created; once the real key is in, the preprovision hook sets
//   GROQ_KEY_IN_VAULT=true and the placeholder is never written again.
//
//   Soft delete is on (Azure no longer lets it be off) with a 7-day window;
//   purge protection is off, so `azd down --purge` can remove the vault at once
//   in dev. Production turns purge protection on.
//
// EXPECTED DEV COST
//   ~$0. Standard tier, $0.03 per 10,000 operations (Azure Retail Prices API,
//   Central India, "Key Vault Standard Operations", read 9 Oct 2026). Dev makes
//   a few hundred reads a month.

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

@description('Principal ids that may read secrets: the two apps\' identities.')
param readerPrincipalIds array

@description('The owner running azd (AZURE_PRINCIPAL_ID): may write secrets from the hooks. Empty skips it.')
param ownerPrincipalId string = ''

@description('True once the real Groq key is in the vault; then the placeholder is not written.')
param groqKeyInVault bool = false

@secure()
@description('The PostgreSQL administrator password (azd secretOrRandomPassword), kept here so azd reads the same one back next time.')
param postgresAdminPassword string

var secretsUser = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
var secretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7' // Key Vault Secrets Officer

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Off in dev so `azd down --purge` can delete the vault at once. Setting this
    // property to false is refused by ARM; leaving it out is how it stays off.
    // enablePurgeProtection: true   <- production
    publicNetworkAccess: 'Enabled'
  }
}

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in readerPrincipalIds: {
    scope: vault
    name: guid(vault.id, principalId, secretsUser)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUser)
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]

resource owner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(ownerPrincipalId)) {
  scope: vault
  name: guid(vault.id, ownerPrincipalId, secretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsOfficer)
    principalId: ownerPrincipalId
    // No principalType: the owner is a user locally and a service principal
    // when a pipeline deploys (Tier 4).
  }
}

resource groqPlaceholder 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!groqKeyInVault) {
  parent: vault
  name: 'groq-api-key'
  tags: {
    placeholder: 'true'
  }
  properties: {
    value: 'set-by-the-postprovision-hook'
  }
}

resource postgresPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
  }
}

output name string = vault.name
output id string = vault.id
output uri string = vault.properties.vaultUri
