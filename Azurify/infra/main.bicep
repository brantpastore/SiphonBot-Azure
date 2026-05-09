param location string = resourceGroup().location
param acrName string = 'myacr${uniqueString(resourceGroup().id)}'
param storageName string = toLower('st${uniqueString(resourceGroup().id)}')
param logWorkspaceName string = 'log-${uniqueString(resourceGroup().id)}'
param appInsightsName string = 'appi-${uniqueString(resourceGroup().id)}'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2022-09-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
  }
}

resource log 'Microsoft.OperationalInsights/workspaces@2021-06-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: log.id
  }
  dependsOn: [
    log
  ]
}

// Container Apps managed environment uses the Microsoft.App provider
// (resource type: Microsoft.App/managedEnvironments). Use an API version
// supported in this subscription and location.
resource containerEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'containerapps-env-${uniqueString(resourceGroup().id)}'
  location: location
  properties: {}
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output storageAccountName string = storage.name
output appInsightsId string = appInsights.id
output containerAppsEnvironmentId string = containerEnv.id

@description('Name for the Function App')
param functionAppName string = 'siphonbot-func-${uniqueString(resourceGroup().id)}'

// App Service plan for Functions (Consumption SKU Y1)
resource functionPlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${functionAppName}-plan'
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

// Function App with system-assigned identity
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
  }
  // dependencies are implied by resource references (serverFarmId, storage, appInsights)
}

// Service Bus namespace and queue for decoupling
param serviceBusName string = 'siphonbus${uniqueString(resourceGroup().id)}'
resource sbNamespace 'Microsoft.ServiceBus/namespaces@2021-11-01' = {
  name: serviceBusName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
}

resource sbQueue 'Microsoft.ServiceBus/namespaces/queues@2021-11-01' = {
  parent: sbNamespace
  name: 'siphon-queue'
  properties: {
    enablePartitioning: false
  }
}

// User-assigned managed identity for Container Apps and other resources
resource uai 'Microsoft.ManagedIdentity/userAssignedIdentities@2018-11-30' = {
  name: 'siphonbot-uai-${uniqueString(resourceGroup().id)}'
  location: location
}

// Key Vault to store secrets (will grant access to Function and UAI via access policies)
param keyVaultName string = 'kv-${uniqueString(resourceGroup().id)}'
resource keyVault 'Microsoft.KeyVault/vaults@2022-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableSoftDelete: true
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: true
    accessPolicies: [
      // Add the function app principal as a policy so it can read secrets
      {
        tenantId: subscription().tenantId
        objectId: functionApp.identity.principalId
        permissions: {
          secrets: [ 'get', 'list' ]
        }
      }
      // Add the user-assigned identity so it can also read secrets
      {
        tenantId: subscription().tenantId
        objectId: uai.properties.principalId
        permissions: {
          secrets: [ 'get', 'list' ]
        }
      }
    ]
  }
  dependsOn: [
    functionApp
    uai
  ]
}

output functionAppName string = functionApp.name
output functionPrincipalId string = functionApp.identity.principalId
output serviceBusNamespace string = sbNamespace.name
output serviceBusQueue string = sbQueue.name
output keyVaultNameOutput string = keyVault.name

// Role assignment to allow the user-assigned identity to pull images from ACR
// Uses the built-in AcrPull role definition id
var acrPullRoleDef = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2020-10-01-preview' = {
  name: guid(acr.id, uai.id, 'acrPull')
  properties: {
    roleDefinitionId: acrPullRoleDef
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
  }
  scope: acr
}

output userAssignedIdentityId string = uai.id
output userAssignedIdentityPrincipalId string = uai.properties.principalId

// Grant the user-assigned identity access to Blob Storage (Storage Blob Data Contributor)
// GUID corresponds to the built-in 'Storage Blob Data Contributor' role.
var storageBlobDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2020-10-01-preview' = {
  name: guid(storage.id, uai.id, 'storageBlob')
  properties: {
    roleDefinitionId: storageBlobDataContributor
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
  }
  scope: storage
}
