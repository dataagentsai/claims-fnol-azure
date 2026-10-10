// The motor-claims FNOL agent's dev environment on Azure (Tier 3).
//
// Subscription-scoped: creates the resource group rg-claims-fnol-dev and
// everything in it, one module per resource (infra/modules/*.bicep, each
// starting with what it is, which concern it serves and what it costs).
// `azd up` runs it; `azd down --purge` removes all of it.
//
// Order: the $10 budget is created first. `identities` depends on it, and every
// other module reads an identity or the vault, so ARM starts nothing before
// the budget exists. Identities and Key Vault come next, so each app's and
// APIM's role is in place before the resource that uses it.
//
// Outputs are written by azd to .azure/<env>/.env, and the apps get the same
// names as environment variables, which config/azure.yaml references with
// {env: NAME}. tests/test_infra.py holds the two lists equal.

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('azd environment name (AZURE_ENV_NAME), e.g. "dev".')
param environmentName string

@description('Region for everything except Content Safety (AZURE_LOCATION).')
param location string = 'centralindia'

@description('Content Safety is not offered in Central India; South India is the nearest region that has it.')
param contentSafetyLocation string = 'southindia'

@description('The signed-in owner\'s object id (azd sets AZURE_PRINCIPAL_ID).')
param principalId string = ''

@description('The owner\'s sign-in name, shown as the PostgreSQL Entra administrator.')
param principalName string = ''

@description('Where the budget alerts and APIM\'s publisher mail go.')
param budgetContactEmail string

@description('First day of the budget period.')
param budgetStartDate string = utcNow('yyyy-MM-01T00:00:00Z')

@description('The owner\'s public IP for psql from the Mac. Empty: none.')
param ownerIpAddress string = ''

@description('True once the real Groq key is in Key Vault (the preprovision hook sets GROQ_KEY_IN_VAULT).')
param groqKeyInVault bool = false

@secure()
@description('PostgreSQL administrator password: azd\'s secretOrRandomPassword (main.parameters.json).')
param postgresAdminPassword string

@description('Image the agent app starts with until `azd deploy` sets the real one.')
param agentImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Image the claims system starts with until `azd deploy` sets the real one.')
param claimsSystemImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

var resourceGroupName = 'rg-claims-fnol-dev'
var token = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  project: 'claims-fnol'
}
var agentPort = 8000
var claimsPort = 9050

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module budget 'modules/budget.bicep' = {
  name: 'budget'
  scope: rg
  params: {
    contactEmail: budgetContactEmail
    startDate: budgetStartDate
    amount: 10
  }
}

module identities 'modules/identities.bicep' = {
  name: 'identities'
  scope: rg
  params: {
    location: location
    tags: tags
    agentIdentityName: 'id-fnol-agent'
    claimsIdentityName: 'id-fnol-claims'
    apimIdentityName: 'id-fnol-apim'
  }
  dependsOn: [budget]
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'kv-fnol-${token}'
    readerPrincipalIds: [
      identities.outputs.agentPrincipalId
      identities.outputs.claimsPrincipalId
      identities.outputs.apimPrincipalId
    ]
    ownerPrincipalId: principalId
    groqKeyInVault: groqKeyInVault
    postgresAdminPassword: postgresAdminPassword
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    workspaceName: 'log-fnol-${token}'
    appInsightsName: 'appi-fnol-${token}'
    keyVaultName: keyVault.outputs.name
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'pg-fnol-${token}'
    administratorPassword: postgresAdminPassword
    entraAdminObjectId: principalId
    entraAdminName: principalName
    ownerIpAddress: ownerIpAddress
    keyVaultName: keyVault.outputs.name
  }
}

module contentSafety 'modules/contentsafety.bicep' = {
  name: 'contentsafety'
  scope: rg
  params: {
    location: contentSafetyLocation
    tags: tags
    name: 'cs-fnol-${token}'
    userPrincipalId: identities.outputs.apimPrincipalId
  }
}

module appConfig 'modules/appconfig.bicep' = {
  name: 'appconfig'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'appcs-fnol-${token}'
    readerPrincipalId: identities.outputs.agentPrincipalId
    ownerPrincipalId: principalId
  }
}

module apim 'modules/apim.bicep' = {
  name: 'apim'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'apim-fnol-${token}'
    publisherEmail: budgetContactEmail
    identityId: identities.outputs.apimId
    identityClientId: identities.outputs.apimClientId
    keyVaultName: keyVault.outputs.name
    appInsightsName: monitoring.outputs.appInsightsName
    contentSafetyEndpoint: contentSafety.outputs.endpoint
  }
}

module environment 'modules/containerapps-env.bicep' = {
  name: 'containerapps-env'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'cae-fnol-${token}'
    logAnalyticsWorkspaceName: monitoring.outputs.workspaceName
  }
}

module claimsSystem 'modules/containerapp.bicep' = {
  name: 'claims-system'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'ca-fnol-claims'
    serviceName: 'claims-system'
    environmentId: environment.outputs.id
    identityId: identities.outputs.claimsId
    identityClientId: identities.outputs.claimsClientId
    image: claimsSystemImage
    targetPort: claimsPort
    external: false
    cpu: '0.25'
    memory: '0.5Gi'
    keyVaultUri: keyVault.outputs.uri
    keyVaultSecrets: [
      { name: 'claims-database-url', secret: 'claims-database-url', variable: 'CLAIMS_DATABASE_URL' }
      // The claims system checks a payout against the agent's own approval
      // records (F-23, A3), on a read-only connection. A4 gives it a role that
      // can only read agent_state.approvals; until then, the agent's URL.
      { name: 'agent-database-url', secret: 'agent-database-url', variable: 'CLAIMS_RECORDS_DATABASE_URL' }
    ]
  }
  dependsOn: [postgres]
}

module agent 'modules/containerapp.bicep' = {
  name: 'agent'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'ca-fnol-agent'
    serviceName: 'agent'
    environmentId: environment.outputs.id
    identityId: identities.outputs.agentId
    identityClientId: identities.outputs.agentClientId
    image: agentImage
    targetPort: agentPort
    external: true
    cpu: '0.5'
    memory: '1Gi'
    keyVaultUri: keyVault.outputs.uri
    // The overlay reads its secrets from Key Vault itself (secrets: key-vault);
    // the apps get only the non-secret names below.
    env: [
      { name: 'CLAIMS_FNOL_ENV', value: 'azure' }
      { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
      { name: 'AZURE_KEY_VAULT_ENDPOINT', value: keyVault.outputs.uri }
      { name: 'APIM_GROQ_BASE_URL', value: apim.outputs.groqBaseUrl }
      { name: 'CLAIMS_MCP_URL', value: '${claimsSystem.outputs.url}/mcp' }
      { name: 'AZURE_APP_CONFIGURATION_ENDPOINT', value: appConfig.outputs.endpoint }
    ]
  }
  dependsOn: [postgres]
}

// azd writes these to .azure/<env>/.env. The first block is also each app's
// environment (above), under the same names, and config/azure.yaml reads them.
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_KEY_VAULT_ENDPOINT string = keyVault.outputs.uri
output APIM_GROQ_BASE_URL string = apim.outputs.groqBaseUrl
output CLAIMS_MCP_URL string = '${claimsSystem.outputs.url}/mcp'
output AZURE_APP_CONFIGURATION_ENDPOINT string = appConfig.outputs.endpoint

// For the hooks, the README and the portal tour.
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_APIM_NAME string = apim.outputs.name
output AZURE_POSTGRES_SERVER string = postgres.outputs.name
output AZURE_POSTGRES_HOST string = postgres.outputs.host
output AZURE_CONTENT_SAFETY_ENDPOINT string = contentSafety.outputs.endpoint
output AZURE_CONTAINER_APPS_ENVIRONMENT string = environment.outputs.name
output AGENT_URL string = agent.outputs.url
