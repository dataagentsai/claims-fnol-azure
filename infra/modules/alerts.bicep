// WHAT IT IS
//   Email alerts on the agent's own telemetry: one action group (email to the
//   owner) and six log alert rules, each a KQL query over the Log Analytics
//   workspace App Insights writes to, run every 15 minutes. A rule fires when its
//   query returns any row in the last 15 minutes:
//     turn-failed           a turn ended Failed ("I am having trouble answering")
//     agent-not-starting    a container exited with code 1, or printed a traceback
//                           (F-118: the new revision failed and nobody knew)
//     model-calls-failing   APIM answered a model call with 4xx/5xx, or the agent's
//                           model call failed
//     content-safety        Content Safety blocked a prompt or completion, or was
//                           unavailable (the F0 quota spent: calls pass unscreened)
//     person-needed         a hand-off was raised, or a payout is waiting for a
//                           claims handler — someone has to act
//     check-blocked         an inline check blocked (injection, PII, a promise of
//                           cover, money in £/$/€…): an attack or a rule misfiring
//
// WHICH CONCERN IT SERVES
//   Observability (deck: rule → finding → alert; PLAN Tier 4b B3 "log alerts").
//   Until this, App Insights only collected: the $10 budget was the only alert.
//   The online watch rules (findings table, Workbook, canary) are still Tier 4b.
//
// EXPECTED DEV COST
//   ~$3/month: log search alert rules at 15-minute frequency are $0.50 a rule a
//   month (Azure Retail Prices API, Central India, "Alerts System Log Monitored at
//   15 Minute Frequency", read 14 Oct 2026); emails are free for the first 1,000
//   a month ("Emails"). Five-minute rules would be $1.50 each.
//
// WHERE TO LOOK (portal tour)
//   Monitor > Alerts (fired alerts) · Monitor > Alerts > Alert rules (these six) ·
//   Monitor > Alerts > Action groups > ag-fnol-owner.

targetScope = 'resourceGroup'

param location string

param tags object = {}

@description('The Log Analytics workspace the rules query (App Insights is workspace-based).')
param workspaceId string

@description('Who gets the alert emails.')
param email string

@description('How often each rule runs and how far back it looks, ISO 8601.')
param frequency string = 'PT15M'

resource owner 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-fnol-owner'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'fnol-alerts'
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: email, useCommonAlertSchema: true }
    ]
  }
}

var rules = [
  {
    key: 'turn-failed'
    title: 'Claims agent: a turn failed'
    description: 'A policyholder got "I am having trouble answering right now". Open the trace by its SessionId in App Insights > Transaction search.'
    severity: 1
    query: '''
AppDependencies
| where Name == "agent.turn" and tostring(Properties["agent.turn.result"]) == "failed"
| project TimeGenerated, conversation = SessionId, run = tostring(Properties["agent.run.id"])
'''
  }
  {
    key: 'agent-not-starting'
    title: 'Claims agent: a container crashed or failed to start'
    description: 'A container exited with code 1 or printed a Python traceback. Container Apps > Revisions and replicas, and the console log.'
    severity: 1
    query: '''
union
  (ContainerAppSystemLogs_CL | where Reason_s == "ContainerTerminated" and Log_s has "exit code '1'"
   | project TimeGenerated, app = ContainerAppName_s, revision = RevisionName_s, what = Log_s),
  (ContainerAppConsoleLogs_CL | where Log_s startswith "Traceback" or Log_s has "Error:"
   | project TimeGenerated, app = ContainerAppName_s, revision = RevisionName_s, what = Log_s)
'''
  }
  {
    key: 'model-calls-failing'
    title: 'Claims agent: model calls failing'
    description: 'APIM answered a chat-completions call with an error, or the agent\'s model call failed. A 403 can be a Content Safety block; a 429 the rate limit; a 5xx Groq.'
    severity: 2
    query: '''
union
  (AppRequests | where Name has "chat/completions" and not(ResultCode startswith "2")
   | project TimeGenerated, what = strcat("APIM returned ", ResultCode)),
  (AppDependencies | where Name == "gen_ai.chat" and Success == false
   | project TimeGenerated, what = "the agent's model call failed")
'''
  }
  {
    key: 'content-safety'
    title: 'Claims agent: Content Safety blocked a call, or is unavailable'
    description: 'block:<category> means a prompt or completion was refused at the gateway; unavailable means it was not screened (F0 quota spent, or the service down).'
    severity: 2
    query: '''
AppDependencies
| where Name == "gen_ai.chat"
| extend verdict = tostring(Properties["agent.gateway.x-content-safety"])
| where verdict has "block" or verdict has "unavailable"
| project TimeGenerated, verdict
'''
  }
  {
    key: 'person-needed'
    title: 'Claims agent: a policyholder is waiting for a person'
    description: 'A conversation was handed to a person, or a payout above the automatic limit is waiting. Open /ops/desk.'
    severity: 3
    query: '''
union
  (AppDependencies | where Name == "agent.escalation.raise"
   | project TimeGenerated, what = strcat("hand-off ", tostring(Properties["agent.escalation.id"]), " (", tostring(Properties["agent.escalation.rule_id"]), ")")),
  (AppDependencies | where Name == "agent.turn" and tostring(Properties["agent.turn.result"]) == "needs_approval"
   | project TimeGenerated, what = strcat("payout waiting, conversation ", SessionId))
'''
  }
  {
    key: 'check-blocked'
    title: 'Claims agent: an inline check blocked a reply or a call'
    description: 'A rule refused something: injection phrasing, personal data, a promise of cover, foreign currency, a write the policyholder did not ask for. Read it: an attack, or a rule misfiring.'
    severity: 3
    query: '''
AppDependencies
| where Name == "agent.policy" and isnotempty(tostring(Properties["agent.policy.blocked_by"]))
| project TimeGenerated, rule = tostring(Properties["agent.policy.blocked_by"]), position = tostring(Properties["agent.policy.position"])
'''
  }
]

resource alert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [for r in rules: {
  name: 'alert-fnol-${r.key}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: r.title
    description: r.description
    severity: r.severity
    enabled: true
    scopes: [workspaceId]
    evaluationFrequency: frequency
    windowSize: frequency
    criteria: {
      allOf: [
        {
          query: r.query
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: false
    actions: {
      actionGroups: [owner.id]
      customProperties: { project: 'claims-fnol' }
    }
  }
}]

output actionGroupId string = owner.id
output ruleNames array = [for (r, i) in rules: alert[i].name]
