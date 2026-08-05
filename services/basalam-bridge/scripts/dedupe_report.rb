# گزارش فقط-خواندنی: چه چیزی را dedupe_inbox13.rb حذف خواهد کرد. چیزی نمی‌نویسد.
inbox = Inbox.find(13)
dupes = inbox.messages.reorder(nil).where.not(source_id: nil)
             .group(:conversation_id, :source_id).having('count(*) > 1').count

victims = []
keepers = []
dupes.each_key do |conversation_id, source_id|
  rows = inbox.messages.reorder(:id).where(conversation_id: conversation_id, source_id: source_id).to_a
  keep = rows.find { |m| m.content_attributes['external_created_at'].present? } || rows.first
  keepers << keep
  victims.concat(rows.reject { |m| m.id == keep.id }.map(&:id))
end

mirror = inbox.messages.reorder(nil).where(source_id: 'bslm:1231237861').pluck(:id)
victims.concat(mirror) if inbox.messages.reorder(nil).where(source_id: nil, message_type: 1).exists?
victims.uniq!

puts "کل پیام اینباکس ۱۳: #{inbox.messages.count}"
puts "گروه‌های تکراری: #{dupes.size}"
puts "نامزد حذف: #{victims.size} | گفتگوها: #{Message.where(id: victims).reorder(nil).distinct.pluck(:conversation_id).inspect}"
puts '--- می‌ماند ---'
keepers.first(5).each do |m|
  puts "  id=#{m.id} ext=#{m.content_attributes['external_created_at'].present?} | #{m.content.to_s.gsub(/\s+/, ' ')[0, 45]}"
end
puts '--- حذف می‌شود ---'
Message.where(id: victims).reorder(:id).limit(5).each do |m|
  puts "  id=#{m.id} ext=#{m.content_attributes['external_created_at'].present?} | #{m.content.to_s.gsub(/\s+/, ' ')[0, 45]}"
end
