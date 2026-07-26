class AddChannelLinksToChannelWebWidgets < ActiveRecord::Migration[7.1]
  def change
    add_column :channel_web_widgets, :channel_links, :jsonb, default: []
  end
end
