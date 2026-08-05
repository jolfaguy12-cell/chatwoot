# Creates (idempotently) the Behdashtik AI AgentBot, attaches it to the inbox
# and prints the credentials the AI service needs.
#
# Run inside the Chatwoot container:
#   docker exec chatwoot-rails-1 bundle exec rails runner /app/../scripts/register_agent_bot.rb
# (or copy this file into the bind-mounted repo first — see docs/runbook.md)

account = Account.find(ENV.fetch('AI_ACCOUNT_ID', '2').to_i)
inbox = account.inboxes.find(ENV.fetch('AI_INBOX_ID', '1').to_i)
outgoing_url = ENV.fetch('AI_WEBHOOK_URL', 'https://support.behdashtik.ir/ai-agent/webhooks/chatwoot')

bot = account.agent_bots.find_or_create_by!(name: 'Behdashtik AI Assistant') do |b|
  b.description = 'AI customer-support agent (products, orders, tracking, policies)'
  b.outgoing_url = outgoing_url
  b.bot_type = :webhook
end
bot.update!(outgoing_url: outgoing_url) if bot.outgoing_url != outgoing_url
bot.regenerate_secret if bot.secret.blank?

agent_bot_inbox = AgentBotInbox.find_or_initialize_by(inbox: inbox)
agent_bot_inbox.assign_attributes(agent_bot: bot, account: account, status: :active)
agent_bot_inbox.save!

puts "AGENT_BOT_ID=#{bot.id}"
puts "BOT_ACCESS_TOKEN=#{bot.access_token.token}"
puts "BOT_WEBHOOK_SECRET=#{bot.secret}"
puts "INBOX=#{inbox.name} (id #{inbox.id}) status=#{agent_bot_inbox.status}"
