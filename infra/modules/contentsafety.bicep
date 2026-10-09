// WHAT IT IS
//   Azure AI Content Safety: a service that scores text for hate, sexual,
//   violence and self-harm content (0-7 severity), and "Prompt Shields", which
//   looks for a user trying to override the model's instructions.
//
// WHICH CONCERN IT SERVES
//   The Azure-provided inline check of Phase 1 (PLAN.html, "Azure-provided
//   inline checks"): with Groq behind APIM there are no Foundry guardrails, so
//   APIM sends each prompt and each completion here (infra/policies/
//   groq-api.xml) and refuses the call on a high score. Its verdicts are
//   logged for the guardrail_log plug-in (Tier 4).
//
// REGION
//   South India, not Central India: Content Safety's region table lists
//   southindia (content harms and prompt shields) and no other Indian region
//   (learn.microsoft.com/azure/ai-services/content-safety/region-availability,
//   updated 18 Sep 2026, read 9 Oct 2026). Text crosses to South India and back.
//
// AUTH
//   Keys disabled (disableLocalAuth): APIM calls it with its own managed
//   identity, which holds "Cognitive Services User" here.
//
// EXPECTED DEV COST
//   $0 on F0: 5,000 text records a month, 5 requests a second
//   (azure.microsoft.com/pricing/details/cognitive-services/content-safety and
//   the Content Safety overview's rate table, read 9 Oct 2026). Each model call
//   is two to three records (prompt shield, prompt, completion); past 5,000 a
//   month F0 refuses (429) and APIM's policy lets the call through unscreened,
//   flagged on the trace. Azure allows a limited number of free (F0) accounts
//   of a kind per subscription; if one exists already, provisioning fails here.

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

@description('APIM\'s identity: calls the service (Cognitive Services User).')
param userPrincipalId string

var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: 'F0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: name // an Entra token needs the custom subdomain endpoint
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource user 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentSafety
  name: guid(contentSafety.id, userPrincipalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: userPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output id string = contentSafety.id
output name string = contentSafety.name
output endpoint string = contentSafety.properties.endpoint
