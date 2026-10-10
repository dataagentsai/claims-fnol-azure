// WHAT IT IS
//   One container app: a container image run by Container Apps, with HTTPS
//   ingress, its own user-assigned managed identity, and its secrets taken
//   from Key Vault by reference (Container Apps fetches them with that
//   identity; the value never appears in this template, a parameter or a
//   file). Used twice by main.bicep:
//     agent          the claims agent (claims_fnol_app): external ingress, the
//                    public HTTPS URL a policyholder and the handler open
//     claims-system  the claims system's MCP server (claims_system): internal
//                    ingress only, reachable from the agent inside the
//                    environment and from nowhere else
//
// WHICH CONCERN IT SERVES
//   Hosting (decision D) and tool_runtime (our MCP server on Container Apps).
//   Both scale to zero (min 0, max 1): a request wakes a replica (a cold start
//   of several seconds), and an idle app bills nothing.
//
// EXPECTED DEV COST
//   ~$0 inside the monthly free grant: "The first 180,000 vCPU-seconds,
//   360,000 GiB-seconds, and 2 million requests per subscription per month are
//   free" (azure.microsoft.com/pricing/details/container-apps, read 9 Oct
//   2026). Past it: $0.000024 per vCPU-second and $0.000003 per GiB-second
//   active, $0.40 per million requests (Azure Retail Prices API, Central India,
//   "Standard vCPU/Memory Active Usage", "Standard Requests", read 9 Oct 2026).
//   The agent at 0.5 vCPU / 1 GiB is ~100 hours a month of activity inside the
//   grant; the claims system at 0.25 vCPU / 0.5 GiB about twice that.
//
// DEPLOYING THE IMAGE
//   Provisioning starts the image named here (a public placeholder until the
//   real ones exist). `azd deploy` then swaps in the image azure.yaml names
//   (GitHub Container Registry), found by the tag azd-service-name. No Azure
//   Container Registry is needed for a public image (azd's container_helper.go:
//   "If we don't have a registry specified and the service does not reference
//   a project path then we are referencing a public/pre-existing image").

targetScope = 'resourceGroup'

param location string
param name string
param tags object = {}

@description('The azd service name this app is deployed as (azure.yaml services key).')
param serviceName string

param environmentId string
param identityId string
param identityClientId string

param image string
param targetPort int
param external bool

@description('Single for a steady app; Multiple lets two revisions take traffic at once, for a canary (Tier 4a A7). New revisions start with no traffic in Multiple mode, so the latest is given 100% until someone splits it.')
@allowed(['Single', 'Multiple'])
param revisionsMode string = 'Single'

@description('vCPU, as a string (Bicep has no decimal literals): 0.25, 0.5, ...')
param cpu string = '0.25'
param memory string = '0.5Gi'

param command array = []
param args array = []

@description('Plain environment variables: [{name, value}].')
param env array = []

@description('Secrets from Key Vault: [{name (container secret and env), secret (vault secret name), variable (env name)}].')
param keyVaultSecrets array = []

param keyVaultUri string

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: revisionsMode
      ingress: {
        external: external
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: revisionsMode == 'Multiple' ? [
          { latestRevision: true, weight: 100 }
        ] : null
      }
      secrets: [
        for s in keyVaultSecrets: {
          name: s.name
          keyVaultUrl: '${keyVaultUri}secrets/${s.secret}'
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: serviceName
          image: image
          // Empty means the image's own CMD.
          command: empty(command) ? null : command
          args: empty(args) ? null : args
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(
            env,
            [
              // The managed identity's client id: which of the app's identities
              // a token request is for (it has only a user-assigned one).
              { name: 'AZURE_CLIENT_ID', value: identityClientId }
            ],
            map(keyVaultSecrets, s => { name: s.variable, secretRef: s.name })
          )
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output name string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
