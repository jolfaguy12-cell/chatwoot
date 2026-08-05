# بازسازی تاریخچهٔ گفتگوی display_id=42 (اینباکس ۱۲) از روی رونوشت bdsk-ai-agent.
# با insert_all! نوشته می‌شود تا هیچ کال‌بکی اجرا نشود — نه وبهوکی می‌رود، نه AgentBot
# دوباره به پیام‌های بازیابی‌شده پاسخ می‌دهد.
#
# اجرا:
#   docker cp scripts/restore_conv42.rb chatwoot-rails-1:/tmp/
#   docker cp /tmp/.../restore42.json chatwoot-rails-1:/tmp/
#   docker exec chatwoot-rails-1 bundle exec rails runner /tmp/restore_conv42.rb
require 'json'

conv = Conversation.find(47)
raise 'گفتگوی اشتباه' unless conv.display_id == 42 && conv.inbox_id == 12
# content_attributes از نوع json است نه jsonb، پس عملگر ? در دسترس نیست
raise 'قبلاً بازسازی شده' if conv.messages.where("content_attributes::text LIKE '%restored_from%'").exists?

rows = JSON.parse(File.read('/tmp/restore42.json'))
now = Time.current

inserts = rows.map do |r|
  at = Time.zone.parse(r['at'].tr('T', ' '))
  incoming = r['type'] == 'incoming'
  {
    account_id: conv.account_id, inbox_id: conv.inbox_id, conversation_id: conv.id,
    message_type: incoming ? 0 : 1,
    content: r['content'], private: false, status: 0,
    sender_type: incoming ? 'Contact' : nil,
    sender_id: incoming ? conv.contact_id : nil,
    content_attributes: { restored_from: 'bdsk-ai-agent transcript' }.to_json,
    created_at: at, updated_at: at
  }
end

inserts << {
  account_id: conv.account_id, inbox_id: conv.inbox_id, conversation_id: conv.id,
  message_type: 1, private: true, status: 0,
  sender_type: nil, sender_id: nil,
  content: '⚠️ تاریخچهٔ این گفتگو بر اثر یک خطای عملیاتی حذف شد و از روی رونوشت سرویس هوش مصنوعی ' \
           'بازسازی شده است. ۱۰ پیام متنی بازیابی شد؛ کارت‌های محصول و پیام‌های فعالیت قابل بازیابی نبودند.',
  content_attributes: {}.to_json, created_at: now, updated_at: now
}

Message.insert_all!(inserts)
conv.update_columns(last_activity_at: conv.messages.maximum(:created_at))
puts({ restored: inserts.size, total_now: conv.messages.count }.to_json)
