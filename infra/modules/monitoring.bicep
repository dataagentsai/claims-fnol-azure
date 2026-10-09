// WHAT IT IS
//   A Log Analytics workspace (where logs and traces are stored and queried with
//   KQL) and Application Insights on top of it (the trace view: one conversation
//   as a tree of spans, the model call inside it, failures and timings).
//
// WHICH CONCERN IT SERVES
//   Telemetry (stack binding `telemetry: azure-monitor-otel`). The agent's own
//   agent.* spans arrive through the OpenTelemetry distro; APIM's token metrics
//   (llm-emit-token-metric) arrive as custom metrics in the same component.
//
// EXPECTED DEV COST
//   ~$0. Ingestion is free below 5 GB a month per billing account, then $3.22/GB
//   (Azure Retail Prices API, Central India, "Analytics Logs Data Ingestion",
//   read 9 Oct 2026). The daily cap below (0.1 GB) keeps a runaway logger to at
//   most ~3 GB a month, inside the free 5 GB. Retention: 30 days, which is
//   included; longer retention is $0.14/GB/month.
//
// WHERE TO LOOK (portal tour)
//   Application Insights > Transaction search: one conversation end to end.
//   Logs: `traces | where customDimensions has "agent."`.

targetScope = 'resourceGroup'

param location string
param workspaceName string
param appInsightsName string
param tags object = {}

@description('The vault the connection string is written to (secret appinsights-connection-string).')
param keyVaultName string

@description('Daily ingestion cap in GB, as a string (Bicep has no decimal literals).')
param dailyCapGb string = '0.1'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: json(dailyCapGb)
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Not a secret by Microsoft's definition, but it lets anyone write telemetry
// here, so it travels through Key Vault and never through an output.
resource connectionString 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'appinsights-connection-string'
  properties: {
    value: appInsights.properties.ConnectionString
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
