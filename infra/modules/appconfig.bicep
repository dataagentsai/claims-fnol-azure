// WHAT IT IS
//   Azure App Configuration: settings and feature flags kept outside the image,
//   changed in the portal without a redeploy. It holds two of this agent's
//   numbers:
//     payout_limit_inr     25000  the automatic payout limit (the AOAS's
//                                 issue_payout.authority; the agent reads it
//                                 from the spec today, this copy is for the
//                                 Tier 5 exercise "change the limit without a
//                                 redeploy")
//     online_sample_rate   1.0    the share of turns the online checks judge
//                                 (evaluators.yaml, position `online`)
//
// WHICH CONCERN IT SERVES
//   Config (stack binding `config: app-configuration`). FINDINGS F-35: the
//   library has no `config` port yet, so nothing reads these at run time; the
//   store exists so Tier 5 has somewhere to change them, and the agent's
//   identity can already read it ("App Configuration Data Reader").
//
// EXPECTED DEV COST
//   $0: Free tier, $0.00/day (Azure Retail Prices API, Central India, "Free
//   Instance", read 9 Oct 2026). One Free store per subscription per region.

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

@description('The agent identity\'s principal id: reads settings.')
param readerPrincipalId string

@description('The owner (AZURE_PRINCIPAL_ID): edits settings in the portal tour. Empty skips it.')
param ownerPrincipalId string = ''

param payoutLimitInr int = 25000

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

resource payoutLimit 'Microsoft.AppConfiguration/configurationStores/keyValues@2023-03-01' = {
  parent: store
  name: 'payout_limit_inr'
  properties: {
    value: string(payoutLimitInr)
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

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: store
  name: guid(store.id, readerPrincipalId, dataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dataReader)
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
  }
}

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
