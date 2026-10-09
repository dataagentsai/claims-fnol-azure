// WHAT IT IS
//   A Container Apps environment: the shared boundary (network, logs, DNS) the
//   two apps run in. Container Apps runs our containers without servers to
//   manage and scales each app to zero replicas when nobody is calling it.
//
// WHICH CONCERN IT SERVES
//   Hosting (stack `x_hosting: azure-container-apps`, architecture decision D):
//   the agent and the claims system's MCP server. Apps in one environment reach
//   each other on internal ingress, so the claims system has no public address.
//
// EXPECTED DEV COST
//   $0 for the environment itself with only the Consumption profile: the
//   pricing page lists no management charge for the Consumption plan (the
//   "Management (hour)" meter belongs to the Dedicated plan)
//   (azure.microsoft.com/pricing/details/container-apps, read 9 Oct 2026).
//   The apps' cost is in containerapp.bicep. App logs go to the Log Analytics
//   workspace, inside its daily cap.

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}
param logAnalyticsWorkspaceName string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output id string = environment.id
output name string = environment.name
output defaultDomain string = environment.properties.defaultDomain
