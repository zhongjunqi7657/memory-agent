param(
    [string]$ApiBase = "http://127.0.0.1:8000",
    [string]$UserKey = "demo-user",
    [string]$SharedPassword = ""
)

$ErrorActionPreference = "Stop"
$headers = @{ "Content-Type" = "application/json" }
if ($SharedPassword) {
    $credentials = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("demo:$SharedPassword")
    )
    $headers["Authorization"] = "Basic $credentials"
}

$conversationId = $null
$messages = @(
    "请记住我喜欢先理解原理再看代码",
    "请记住我当前准备考研",
    "这周我完成了 FastAPI 异步基础和 LangGraph 工具调用练习。",
    "我现在决定暂时不准备考研，优先参加秋招。"
)

foreach ($message in $messages) {
    $payload = @{
        user_key = $UserKey
        content = $message
        conversation_id = $conversationId
    } | ConvertTo-Json
    $requestParameters = @{
        Method = "Post"
        Uri = "$ApiBase/v1/chat"
        Headers = $headers
        Body = $payload
    }
    $result = Invoke-RestMethod @requestParameters
    $conversationId = $result.conversation_id
}

Write-Output "Demo conversation seeded: $conversationId"
Write-Output "Wait for the Worker to process queued extraction jobs before the demo."
