// WHAT IT IS
//   Azure App Configuration: settings and feature flags kept outside the image,
//   changed in the portal or with `az appconfig kv set` without a redeploy. It
//   holds three of this agent's settings:
//     payout.automatic_limit_inr  (label dev)  25000  the automatic payout
//                                 limit. The AOAS's issue_payout.authority is
//                                 its default and its ceiling; the agent and
//                                 the claims system both read this one key
//                                 (Tier 4a A6, FINDINGS F-71) and re-read it
//                                 every 30 s, so lowering it reaches the next
//                                 payout (Tier 5's exercise).
//     agent.enabled  (label dev)  true   the kill switch (Tier 4a A13): false
//                                 makes every new turn the paused reply, with
//                                 no model call, within 30 s; approvals and
//                                 escalations at the desk go on. Only the agent
//                                 reads it. A provision writes this value back
//                                 (FINDINGS F-88).
//     online_sample_rate   1.0    the share of turns the online checks judge
//                                 (evaluators.yaml, position `online`); not
//                                 read yet (F-77)
//
// WHICH CONCERN IT SERVES
//   Config (stack binding `config: app-configuration`, the harness's
//   `app-configuration` adapter). Each app's identity reads it with "App
//   Configuration Data Reader"; nothing but the owner can write.
//
// EXPECTED DEV COST
//   $0: Free tier, $0.00/day (Azure Retail Prices API, Central India, "Free
//   Instance", read 9 Oct 2026). One Free store per subscription per region.

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

@description('The apps\' identities (the agent, the claims system): each reads settings.')
param readerPrincipalIds array

@description('The owner (AZURE_PRINCIPAL_ID): edits settings in the portal tour. Empty skips it.')
param ownerPrincipalId string = ''

param payoutLimitInr int = 25000

@description('The label both apps read under (config/azure.yaml, config/claims-system/azure.yaml).')
param label string = 'dev'

@description('The kill switch the agent reads before every turn (A13). False pauses it.')
param agentEnabled bool = true

@description('A string, as Bicep has no decimal literals.')
param onlineSampleRate string = '1.0'

var dataReader = '516239f1-63e1-4d78-a4de-a74fb236a071' // App Configuration Data Reader
var dataOwner = '5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b' // App Configuration Data Owner

resource store 'Microsoft.AppConfiguration/configurationStores@2023-03-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'free'
  }
  properties: {
    disableLocalAuth: false // ARM writes the key-values below through the control plane
  }
}

// A key-value's resource name is `<key>$<label>`.
resource payoutLimit 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = {
  parent: store
  name: 'payout.automatic_limit_inr$${label}'
  properties: {
    value: string(payoutLimitInr)
    contentType: 'text/plain'
  }
}

resource killSwitch 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = {
  parent: store
  name: 'agent.enabled$${label}'
  properties: {
    value: agentEnabled ? 'true' : 'false'
    contentType: 'text/plain'
  }
}

resource sampleRate 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = {
  parent: store
  name: 'online_sample_rate'
  properties: {
    value: onlineSampleRate
    contentType: 'text/plain'
  }
}

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for reader in readerPrincipalIds: {
  scope: store
  name: guid(store.id, reader, dataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dataReader)
    principalId: reader
    principalType: 'ServicePrincipal'
  }
}]

resource owner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(ownerPrincipalId)) {
  scope: store
  name: guid(store.id, ownerPrincipalId, dataOwner)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dataOwner)
    principalId: ownerPrincipalId
    // No principalType: the owner is a user locally and a service principal
    // when a pipeline deploys (Tier 4).
  }
}

output name string = store.name
output endpoint string = store.properties.endpoint
