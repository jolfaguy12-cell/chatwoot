# پاک‌سازی پیام‌های تکراری اینباکس ۱۳ (باسلام) و اصلاح مهر زمانی.
#
# تکراری‌ها وقتی ساخته شدند که import_chat یک بار بدون چک «خودمان فرستادیم»
# اجرا شد. آن باگ در scripts/import_chat.py رفع شده؛ این اسکریپت فقط
# آثار باقی‌مانده را جمع می‌کند و یک‌بار مصرف است.
#
# گزارش فقط-خواندنی قبل از اجرا:
#   docker cp scripts/dedupe_report.rb chatwoot-rails-1:/tmp/
#   docker exec chatwoot-rails-1 bundle exec rails runner /tmp/dedupe_report.rb
#
# اجرا:
#   docker cp scripts/dedupe_inbox13.rb chatwoot-rails-1:/tmp/
#   docker exec chatwoot-rails-1 bundle exec rails runner /tmp/dedupe_inbox13.rb
inbox = Inbox.find(13)

# در هر گروه تکراری، نسخه‌ای می‌ماند که مهر زمانی واقعی باسلام را همراه دارد
dupes = inbox.messages.reorder(nil).where.not(source_id: nil)
             .group(:conversation_id, :source_id).having('count(*) > 1').count
victims = []
dupes.each_key do |conversation_id, source_id|
  rows = inbox.messages.reorder(:id).where(conversation_id: conversation_id, source_id: source_id).to_a
  keep = rows.find { |m| m.content_attributes['external_created_at'].present? } || rows.first
  victims.concat(rows.reject { |m| m.id == keep.id }.map(&:id))
end

# آینهٔ پاسخی که خودمان از چت‌وود فرستادیم؛ اصلِ چت‌وودی می‌ماند
if inbox.messages.reorder(nil).where(source_id: nil, message_type: 1).exists?
  victims.concat(inbox.messages.reorder(nil).where(source_id: 'bslm:1231237861').pluck(:id))
end

victims.uniq!
puts "حذف: #{victims.size} پیام از گفتگوهای #{Message.where(id: victims).reorder(nil).distinct.pluck(:conversation_id).inspect}"
Message.where(id: victims).delete_all

# مهر زمانی پیام‌هایی که بعد از اجرای قبلیِ اصلاح وارد شده‌اند
fixed = 0
inbox.messages.find_each do |m|
  ts = m.content_attributes['external_created_at']
  next if ts.blank?

  at = Time.zone.at(ts.to_i)
  next if (m.created_at - at).abs < 1

  m.update_columns(created_at: at, updated_at: at)
  fixed += 1
end

inbox.conversations.find_each do |c|
  last = c.messages.maximum(:created_at)
  c.update_columns(last_activity_at: last, created_at: c.messages.minimum(:created_at)) if last
end

puts({ deleted: victims.size, timestamps_fixed: fixed,
       inbox_messages: inbox.messages.count,
       conv52: Conversation.find(52).messages.count }.to_json)
