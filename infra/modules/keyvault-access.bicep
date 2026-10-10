// WHAT IT IS
//   Read access for one managed identity to named Key Vault secrets, and no
//   others: one "Key Vault Secrets User" role assignment per secret, scoped to
//   that secret, not to the vault. Used once per app by main.bicep.
//
// WHICH CONCERN IT SERVES
//   Secrets and least privilege (Tier 4a A4). Each app's identity can read only
//   its own database login's URL: the agent's identity never the claims
//   system's (claims-database-url, claims-records-database-url), the claims
//   system's never the agent's (agent-database-url), and neither the
//   PostgreSQL administrator's password or the role passwords
//   (postgres-*-password), which only the owner running the hooks reads.
//   tests/test_infra.py holds each list to what the app reads.
//
// EXPECTED DEV COST
//   $0: role assignments are free.

targetScope = 'resourceGroup'

param keyVaultName string

@description('The identity\'s principal id (a user-assigned managed identity).')
param principalId string

@description('The secrets it may read, by name. Each must exist when this deploys.')
param secretNames array

var secretsUser = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = [
  for name in secretNames: {
    parent: vault
    name: name
  }
]

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, i) in secretNames: {
    scope: secrets[i]
    name: guid(vault.id, name, principalId, secretsUser)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUser)
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
