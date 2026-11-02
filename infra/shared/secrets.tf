resource "aws_secretsmanager_secret" "anthropic" {
  name                    = "relay/anthropic-api-key"
  description             = "Claude API key for the Relay backend and the AI SRE agent"
  recovery_window_in_days = 0 # delete immediately on destroy (fine for a learning project)
}

resource "aws_secretsmanager_secret" "slack" {
  name                    = "relay/slack-webhook"
  description             = "Slack incoming webhook URL for Alertmanager and the AI SRE agent"
  recovery_window_in_days = 0
}
