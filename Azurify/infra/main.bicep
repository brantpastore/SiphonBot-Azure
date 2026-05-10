param location string = resourceGroup().location
param acrName string = 'myacr${uniqueString(resourceGroup().id)}'
param logWorkspaceName string = 'log-${uniqueString(resourceGroup().id)}'
param appInsightsName string = 'appi-${uniqueString(resourceGroup().id)}'

@description('Name for the optional Function App')
param functionAppName string = 'siphonbot-func-${uniqueString(resourceGroup().id)}'

@description('Storage account name for Function host storage (lowercase letters and numbers only, 3-24 chars).')
param functionStorageAccountName string = 'siphonbotfuncappstorage'

@description('Set true to deploy Function App resources. Requires functionStorageConnectionString.')
param deployFunctionApp bool = false

@description('Existing Storage connection string for AzureWebJobsStorage (only used when deployFunctionApp=true).')
@secure()
param functionStorageConnectionString string = ''

// Service Bus namespace and queue for decoupling
param serviceBusName string = 'siphonbus${uniqueString(resourceGroup().id)}'

@description('Create AcrPull role assignment for user-assigned identity (disable if it already exists).')
param createUaiAcrPullAssignment bool = true

@description('Optional: principal id of an existing container app to grant AcrPull for (leave empty to skip)')
param containerAppPrincipalId string = ''

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
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
}

resource containerEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'containerapps-env-${uniqueString(resourceGroup().id)}'
  location: location
  properties: {}
}

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

// User-assigned managed identity for Container Apps image pull
resource uai 'Microsoft.ManagedIdentity/userAssignedIdentities@2018-11-30' = {
  name: 'siphonbot-uai-${uniqueString(resourceGroup().id)}'
  location: location
}

// App Service plan and Function App are optional in this profile.
resource functionPlan 'Microsoft.Web/serverfarms@2023-01-01' = if (deployFunctionApp) {
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

resource functionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (deployFunctionApp) {
  name: functionStorageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = if (deployFunctionApp) {
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
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'SERVICE_BUS_QUEUE_NAME'
          value: sbQueue.name
        }
        {
          name: 'AzureWebJobsStorage'
          value: empty(functionStorageConnectionString)
            ? 'DefaultEndpointsProtocol=https;AccountName=${functionStorage!.name};AccountKey=${functionStorage!.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
            : functionStorageConnectionString
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
}

// Role assignment to allow identities to pull images from ACR.
var acrPullRoleDef = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2020-10-01-preview' = if (createUaiAcrPullAssignment) {
  name: guid(acr.id, uai.id, 'acrPull')
  properties: {
    roleDefinitionId: acrPullRoleDef
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
  }
  scope: acr
}

resource containerAppAcrPull 'Microsoft.Authorization/roleAssignments@2020-10-01-preview' = if (containerAppPrincipalId != '') {
  name: guid(acr.id, containerAppPrincipalId, 'containerAppAcr')
  properties: {
    roleDefinitionId: acrPullRoleDef
    principalId: containerAppPrincipalId
    principalType: 'ServicePrincipal'
  }
  scope: acr
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output containerAppsEnvironmentId string = containerEnv.id
output serviceBusNamespace string = sbNamespace.name
output serviceBusQueue string = sbQueue.name
output serviceBusConnectionString string = listkeys('${sbNamespace.id}/AuthorizationRules/RootManageSharedAccessKey', sbNamespace.apiVersion).primaryConnectionString
output userAssignedIdentityId string = uai.id
output userAssignedIdentityPrincipalId string = uai.properties.principalId
output functionAppName string = deployFunctionApp ? functionApp!.name : ''
output functionPrincipalId string = deployFunctionApp ? functionApp!.identity.principalId : ''
output functionStorageAccountName string = deployFunctionApp ? functionStorage!.name : ''
output functionStorageConnectionString string = deployFunctionApp ? 'DefaultEndpointsProtocol=https;AccountName=${functionStorage!.name};AccountKey=${functionStorage!.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}' : ''
