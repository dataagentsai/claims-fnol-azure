// WHAT IT IS
//   Three user-assigned managed identities: the agent app's, the claims
//   system's, and API Management's (it reads the Groq key from Key Vault and
//   calls Content Safety). A managed identity is a login Azure gives a workload, with no
//   password anywhere: the app asks the platform for a token and Azure checks
//   the roles granted to that identity.
//
// WHICH CONCERN IT SERVES
//   Identity for workloads and secrets (stack bindings `identity: entra-id`,
//   `secrets: key-vault`): each app reads only its own secrets from Key Vault,
//   and Container Apps resolves Key Vault references with this identity.
//   User-assigned (not system-assigned) so each identity exists before the
//   resource that uses it, and its roles are in place before that resource's
//   first start: an app's first revision, APIM's first Key Vault named value.
//
// EXPECTED DEV COST
//   $0. Managed identities are free.

targetScope = 'resourceGroup'

param location string
param agentIdentityName string
param claimsIdentityName string
param apimIdentityName string
param tags object = {}

resource agent 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: agentIdentityName
  location: location
  tags: tags
}

resource claims 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: claimsIdentityName
  location: location
  tags: tags
}

resource apim 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: apimIdentityName
  location: location
  tags: tags
}

output agentId string = agent.id
output agentPrincipalId string = agent.properties.principalId
output agentClientId string = agent.properties.clientId
output agentName string = agent.name
output claimsId string = claims.id
output claimsPrincipalId string = claims.properties.principalId
output claimsClientId string = claims.properties.clientId
output apimId string = apim.id
output apimPrincipalId string = apim.properties.principalId
output apimClientId string = apim.properties.clientId
