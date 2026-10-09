// WHAT IT IS
//   Azure API Management (APIM), Consumption tier: the one door every model
//   call goes through. The agent calls APIM with a subscription key; APIM
//   checks the request rate, screens the prompt with Content Safety, adds the
//   Groq key (from Key Vault, never seen by the agent), forwards to Groq's
//   OpenAI-compatible API, screens the completion, and emits token metrics.
//
// WHICH CONCERN IT SERVES
//   The model gateway (stack binding `model: apim-ai-gateway`, architecture
//   decision B: "one door, one identity, one bill"). Moving from Groq to a
//   Foundry deployment is a backend change here, not a code change.
//
// EXPECTED DEV COST
//   ~$0. Consumption: the first 1,000,000 calls a month free, then $0.035 per
//   10,000 (Azure Retail Prices API, Central India, "Consumption Calls", tiers
//   0 and 100 x 10K, read 9 Oct 2026). Dev makes hundreds.
//
// WHAT RUNS ON EACH CALL, AND WHAT DOES NOT (infra/policies/groq-api.xml)
//   Tier support on the Consumption gateway, as Microsoft's docs said on
//   9 Oct 2026:
//   - llm-token-limit: NOT available. "The rate limit by key, quota by key, and
//     LLM token limit policies aren't available in the Consumption tier."
//     https://learn.microsoft.com/azure/api-management/api-management-gateways-overview
//     (Policies, footnote 2; updated 24 Aug 2026). The policy's page lists
//     gateways "classic, v2, self-hosted, workspace":
//     https://learn.microsoft.com/azure/api-management/llm-token-limit-policy
//     (updated 26 Jun 2026).
//   - rate-limit-by-key, the planned fallback: NOT available either (same
//     footnote; https://learn.microsoft.com/azure/api-management/rate-limit-by-key-policy,
//     gateways "classic, v2, self-hosted, workspace", updated 26 Jun 2026).
//   - rate-limit (per subscription): available, "APPLIES TO: All API Management
//     tiers", gateways include consumption
//     https://learn.microsoft.com/azure/api-management/rate-limit-policy
//     (updated 19 Aug 2026). USED: 30 calls a minute. A request rate, not a
//     token rate; the harness's per-turn token and cost ceilings still apply
//     in the agent. FINDINGS F-39.
//   - llm-emit-token-metric: "APPLIES TO: All API Management tiers", gateways
//     include consumption
//     https://learn.microsoft.com/azure/api-management/llm-emit-token-metric-policy
//     (updated 17 Sep 2026). USED. Needs Application Insights with custom
//     metrics (the diagnostic below sets metrics: true).
//   - llm-content-safety: the page contradicts itself: "APPLIES TO: Developer |
//     Basic | Basic v2 | Standard | Standard v2 | Premium | Premium v2" (no
//     Consumption) but gateways "classic, v2, consumption, self-hosted,
//     workspace"
//     https://learn.microsoft.com/azure/api-management/llm-content-safety-policy
//     (updated 19 Aug 2026). It also needs a backend authenticated by APIM's
//     managed identity, which the ARM backend schema cannot express (no
//     managed-identity credential in any version up to 2025-09-01-preview:
//     https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/backends).
//     NOT USED. Instead send-request to Content Safety's REST API
//     (text:shieldPrompt and text:analyze, api-version 2024-09-01) with
//     authentication-managed-identity, which the gateways overview lists for
//     Consumption ("Authenticate with managed identity"). FINDINGS F-40.
//   - Key Vault named values and managed identities: "APPLIES TO: All API
//     Management tiers"
//     https://learn.microsoft.com/azure/api-management/api-management-howto-properties
//     https://learn.microsoft.com/azure/api-management/api-management-howto-use-managed-service-identity
//     A named value follows a Key Vault update within four hours; the
//     postprovision hook refreshes it at once.
//   - Consumption limits: policy document 16 KiB, a request may take at most
//     30 seconds (gateways overview, runtime limits). A long completion from
//     Groq that takes longer is cut off.
//
// WHERE TO LOOK (portal tour)
//   APIs > groq > Test (with the subscription key), Named values, Backends;
//   Application Insights > Metrics > namespace claims-fnol (token counts).
//   "API analytics" is not offered on Consumption (gateways overview).

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

param publisherEmail string
param publisherName string = 'claims-fnol dev'

@description('APIM\'s user-assigned identity (identities.bicep): reads Key Vault, calls Content Safety.')
param identityId string
param identityClientId string

param keyVaultName string
param appInsightsName string

@description('Content Safety endpoint, ending in a slash.')
param contentSafetyEndpoint string

@description('Severity (FourSeverityLevels: 0, 2, 4, 6) at or above which a prompt or completion is refused.')
param contentSafetyThreshold int = 4

param groqBaseUrl string = 'https://api.groq.com/openai/v1'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource groqKey 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'groq-api-key'
  properties: {
    displayName: 'groq-api-key'
    secret: true
    keyVault: {
      // No version: APIM follows the latest (the hook's real key).
      secretIdentifier: '${vault.properties.vaultUri}secrets/groq-api-key'
      identityClientId: identityClientId
    }
  }
}

resource csEndpoint 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'content-safety-endpoint'
  properties: {
    displayName: 'content-safety-endpoint'
    value: contentSafetyEndpoint
  }
}

resource csThreshold 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'content-safety-threshold'
  properties: {
    displayName: 'content-safety-threshold'
    value: string(contentSafetyThreshold)
  }
}

resource identityClient 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'apim-identity-client-id'
  properties: {
    displayName: 'apim-identity-client-id'
    value: identityClientId
  }
}

resource groq 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'groq'
  properties: {
    title: 'Groq (OpenAI-compatible)'
    protocol: 'http'
    url: groqBaseUrl
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'groq'
  properties: {
    displayName: 'Groq via the AI gateway'
    path: 'groq/openai/v1'
    protocols: ['https']
    serviceUrl: groqBaseUrl
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'Ocp-Apim-Subscription-Key'
      query: 'subscription-key'
    }
  }
}

resource chatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'chat-completions'
  properties: {
    displayName: 'Chat completions'
    method: 'POST'
    urlTemplate: '/chat/completions'
  }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    // rawxml: APIM's own dialect, where C# expressions keep their < and quotes.
    format: 'rawxml'
    value: loadTextContent('../policies/groq-api.xml')
  }
  dependsOn: [groqKey, csEndpoint, csThreshold, identityClient, groq, logger]
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.id
    credentials: {
      instrumentationKey: appInsights.properties.InstrumentationKey
    }
  }
}

resource diagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    metrics: true // custom metrics, for llm-emit-token-metric
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    verbosity: 'information'
  }
}

// The agent's key to this API. Its value goes straight to Key Vault
// (apim-subscription-key), which the overlay names; it never leaves Azure.
resource agentSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'claims-fnol-agent'
  properties: {
    displayName: 'claims-fnol agent'
    scope: '/apis/${api.name}'
    state: 'active'
  }
}

resource subscriptionKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'apim-subscription-key'
  properties: {
    value: agentSubscription.listSecrets().primaryKey
  }
}

output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
@description('What the overlay\'s model.base_url is: the API\'s path on the gateway.')
output groqBaseUrl string = '${apim.properties.gatewayUrl}/${api.properties.path}'
