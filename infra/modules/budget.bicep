// WHAT IT IS
//   A cost budget on the resource group rg-claims-fnol-dev: $10 a month, with an
//   email to the owner when actual spend reaches 50% and 80%, and when the month's
//   forecast reaches 100%. A budget never stops anything; it only tells you.
//
// WHICH CONCERN IT SERVES
//   Cost (architecture deck, slide 53; PLAN.html Tier 3, "a $10 budget alert
//   before anything else"). The 12-month free offer is used up, so PostgreSQL is
//   the one real cost; this is the tripwire if something else starts billing.
//
// EXPECTED DEV COST
//   $0. Budgets are free (Cost Management).
//
// ORDER
//   main.bicep makes every other module depend on this one, so ARM creates the
//   budget first. Cost data reaches Cost Management with a delay of hours, so an
//   alert is never instant.

targetScope = 'resourceGroup'

@description('Budget name.')
param name string = 'budget-claims-fnol-dev'

@description('Monthly amount in the billing currency (USD for this subscription).')
param amount int = 10

@description('Who gets the alert emails.')
param contactEmail string

@description('First day of the budget period, yyyy-MM-01T00:00:00Z (main.bicep: this month).')
param startDate string

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actual50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: [contactEmail]
      }
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [contactEmail]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [contactEmail]
      }
    }
  }
}

output id string = budget.id
