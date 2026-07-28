# frozen_string_literal: true

# Behdashtik installation branding.
#
# InstallationConfig rows drift back to the upstream Chatwoot defaults whenever
# ConfigLoader runs against an upstream copy of config/installation_config.yml
# (db:seed, an upgrade that overwrites the YAML, a Super Admin edit). This file
# is Behdashtik-owned, so upstream upgrades never touch it: it re-asserts the
# branding on every boot and is the source of truth for these three keys.
#
# Changing the brand name or the widget footer link means editing BRANDING here
# (keep config/installation_config.yml in sync for fresh installs).
BEHDASHTIK_BRANDING = {
  'INSTALLATION_NAME' => 'بهداشتیک',
  'BRAND_NAME' => 'بهداشتیک',
  'WIDGET_BRAND_URL' => 'tel:09124517893'
}.freeze

Rails.application.config.after_initialize do
  next unless ActiveRecord::Base.connection.table_exists?('installation_configs')

  changed = BEHDASHTIK_BRANDING.filter_map do |name, value|
    config = InstallationConfig.find_by(name: name)
    next if config.nil? || config.value == value

    config.update!(value: value)
    name
  end

  if changed.any?
    GlobalConfig.clear_cache
    Rails.logger.info "[BehdashtikBranding] restored installation config: #{changed.join(', ')}"
  end
rescue StandardError => e
  # Boot must never fail on branding (migrations, asset precompile, no DB yet).
  Rails.logger.error "[BehdashtikBranding] Failed to apply branding: #{e.class}"
end
