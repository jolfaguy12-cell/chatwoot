class Behdashtik::VisitorJourneyService
  SENSITIVE_PARAMS = %w[token password key auth session email phone api_key secret code].freeze
  MAX_URL_LENGTH = 2048
  MAX_TITLE_LENGTH = 500

  def initialize(conversation, params)
    @conversation = conversation
    @params = params
  end

  def perform
    content = build_note_content
    ::Messages::MessageBuilder.new(nil, @conversation, { content: content, private: true }).perform
  rescue StandardError => e
    Rails.logger.error "[BehdashtikJourney] Failed to create private note: #{e.class}"
    nil
  end

  # Structured page context for the AI agent: merged (never replaced) into the
  # conversation's custom_attributes so it reaches agent-bot webhook payloads.
  def update_page_context
    url = sanitize_url(@params[:url])
    return if url.blank?

    attributes = {
      'behdashtik_current_url' => url,
      'behdashtik_page_title' => sanitize_text(@params[:title]),
      'behdashtik_page_seen_at' => parse_timestamp(@params[:timestamp]),
      'behdashtik_product_slug' => product_slug_from(url)
    }
    @conversation.update!(custom_attributes: @conversation.custom_attributes.to_h.merge(attributes))
  rescue StandardError => e
    Rails.logger.error "[BehdashtikJourney] Failed to update page context: #{e.class}"
    nil
  end

  private

  def product_slug_from(url)
    match = URI.parse(url).path.match(%r{/product/([^/]+)})
    match ? CGI.unescape(match[1]) : ''
  rescue URI::InvalidURIError
    ''
  end

  def build_note_content
    url = sanitize_url(@params[:url])
    title = sanitize_text(@params[:title])
    referrer = sanitize_url(@params[:referrer_url])
    ts = parse_timestamp(@params[:timestamp])

    lines = ["🗺️ **Page Visit** · #{ts}", "**URL:** #{url}"]
    lines << "**Title:** #{title}" if title.present?
    lines << "**From:** #{referrer}" if referrer.present?
    lines << '_Source: Behdashtik Journey Tracker_'
    lines.join("\n")
  end

  def sanitize_url(raw)
    return '' if raw.blank?

    url_str = raw.to_s.slice(0, MAX_URL_LENGTH)
    uri = URI.parse(url_str)
    return url_str unless uri.query

    clean_params = URI.decode_www_form(uri.query).reject do |key, _|
      SENSITIVE_PARAMS.any? { |s| key.to_s.downcase.include?(s) }
    end

    uri.query = clean_params.any? ? URI.encode_www_form(clean_params) : nil
    uri.to_s
  rescue URI::InvalidURIError
    ''
  end

  def sanitize_text(raw)
    return '' if raw.blank?

    raw.to_s.slice(0, MAX_TITLE_LENGTH).gsub(/<[^>]+>/, '').strip
  end

  def parse_timestamp(raw)
    Time.parse(raw.to_s).utc.strftime('%Y-%m-%d %H:%M:%S UTC')
  rescue ArgumentError, TypeError
    Time.current.utc.strftime('%Y-%m-%d %H:%M:%S UTC')
  end
end
