class Api::V1::Accounts::BehdashtikAi::ProxyController < Api::V1::Accounts::BaseController
  # Thin authenticated pass-through to the Behdashtik AI service admin API.
  # Chatwoot is the auth layer: administrators reach everything; agents only
  # the self-service allowlist below. The service's bearer token never leaves
  # the server.
  # Two agents share this panel: the website assistant and the Basalam shop
  # assistant. `service` picks which one a request is routed to.
  SERVICES = {
    'site' => %w[BEHDASHTIK_AI_SERVICE_URL BEHDASHTIK_AI_ADMIN_TOKEN],
    'basalam' => %w[BEHDASHTIK_AI_BASALAM_SERVICE_URL BEHDASHTIK_AI_BASALAM_ADMIN_TOKEN]
  }.freeze

  AGENT_ALLOWED_PATHS = [
    ['POST', %r{\Atelegram/link_code\z}],
    ['GET', %r{\Atelegram/self\z}],
    ['PATCH', %r{\Atelegram/self\z}],
    ['DELETE', %r{\Atelegram/self\z}],
    ['POST', %r{\Aresponses/\d+/evaluations\z}]
  ].freeze

  FORWARD_TIMEOUT_SECONDS = 150 # test runs / testchat include live LLM calls

  before_action :check_access

  def forward
    response_object = HTTParty.send(
      request.method.downcase,
      "#{service_url}/admin/v1/#{proxy_path}",
      headers: forward_headers,
      query: request.query_parameters.except('proxy_path', 'service'),
      body: forward_body,
      timeout: FORWARD_TIMEOUT_SECONDS
    )
    render plain: response_object.body, status: response_object.code,
           content_type: response_object.headers['content-type'] || 'application/json'
  rescue Errno::ECONNREFUSED, Net::OpenTimeout, Net::ReadTimeout, SocketError => e
    Rails.logger.error "[BehdashtikAI] proxy error: #{e.class}"
    render json: { error: 'ai_service_unavailable' }, status: :service_unavailable
  end

  private

  def proxy_path
    params[:proxy_path].to_s
  end

  def service
    SERVICES.key?(params[:service]) ? params[:service] : 'site'
  end

  def service_url
    ENV.fetch(SERVICES[service].first, '').chomp('/')
  end

  def admin_token
    ENV.fetch(SERVICES[service].last, '')
  end

  def check_access
    return render json: { error: 'ai_service_not_configured' }, status: :service_unavailable if service_url.blank?
    return if Current.account_user&.administrator?
    return if AGENT_ALLOWED_PATHS.any? { |method, pattern| request.method == method && proxy_path.match?(pattern) }

    render json: { error: 'unauthorized' }, status: :unauthorized
  end

  def forward_headers
    {
      'Authorization' => "Bearer #{admin_token}",
      'Content-Type' => 'application/json',
      'X-Chatwoot-User-Id' => current_user.id.to_s,
      'X-Chatwoot-User-Name' => current_user.name.to_s
    }
  end

  def forward_body
    return nil if request.raw_post.blank?
    return request.raw_post unless evaluation_request?

    # attribution for evaluations always comes from the session, never the client
    body = JSON.parse(request.raw_post)
    body['rater_user_id'] = current_user.id
    body['rater_name'] = current_user.name
    body.to_json
  rescue JSON::ParserError
    request.raw_post
  end

  def evaluation_request?
    request.method == 'POST' && proxy_path.match?(%r{\Aresponses/\d+/evaluations\z})
  end
end
