require 'rails_helper'

RSpec.describe 'Behdashtik AI proxy API', type: :request do
  let(:account) { create(:account) }
  let(:admin) { create(:user, account: account, role: :administrator) }
  let(:agent) { create(:user, account: account, role: :agent) }
  let(:service_url) { 'https://ai.example.test' }

  around do |example|
    with_modified_env BEHDASHTIK_AI_SERVICE_URL: service_url, BEHDASHTIK_AI_ADMIN_TOKEN: 'proxy-token' do
      example.run
    end
  end

  describe 'GET /api/v1/accounts/:id/behdashtik_ai/*path' do
    it 'rejects unauthenticated requests' do
      get "/api/v1/accounts/#{account.id}/behdashtik_ai/settings"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'forwards admin requests with the bearer token and user headers' do
      stub = stub_request(:get, "#{service_url}/admin/v1/settings")
             .with(headers: { 'Authorization' => 'Bearer proxy-token',
                              'X-Chatwoot-User-Id' => admin.id.to_s })
             .to_return(status: 200, body: { ai_enabled: true }.to_json,
                        headers: { 'content-type' => 'application/json' })

      get "/api/v1/accounts/#{account.id}/behdashtik_ai/settings",
          headers: admin.create_new_auth_token

      expect(response).to have_http_status(:success)
      expect(response.parsed_body['ai_enabled']).to be(true)
      expect(stub).to have_been_requested
    end

    it 'blocks agents from admin paths' do
      get "/api/v1/accounts/#{account.id}/behdashtik_ai/settings",
          headers: agent.create_new_auth_token
      expect(response).to have_http_status(:unauthorized)
    end

    it 'allows agents to reach telegram self-service' do
      stub_request(:get, "#{service_url}/admin/v1/telegram/self")
        .to_return(status: 200, body: { linked: false }.to_json,
                   headers: { 'content-type' => 'application/json' })

      get "/api/v1/accounts/#{account.id}/behdashtik_ai/telegram/self",
          headers: agent.create_new_auth_token
      expect(response).to have_http_status(:success)
    end

    it 'overwrites evaluation rater identity from the session' do
      stub = stub_request(:post, "#{service_url}/admin/v1/responses/7/evaluations")
             .with(body: hash_including('rater_user_id' => agent.id, 'rater_name' => agent.name))
             .to_return(status: 200, body: { id: 1 }.to_json,
                        headers: { 'content-type' => 'application/json' })

      post "/api/v1/accounts/#{account.id}/behdashtik_ai/responses/7/evaluations",
           params: { dimensions: { correctness: 5 }, rater_user_id: 999, rater_name: 'spoof' }.to_json,
           headers: agent.create_new_auth_token.merge('Content-Type' => 'application/json')

      expect(response).to have_http_status(:success)
      expect(stub).to have_been_requested
    end

    it 'returns 503 when the service is unreachable' do
      stub_request(:get, "#{service_url}/admin/v1/health").to_raise(Errno::ECONNREFUSED)
      get "/api/v1/accounts/#{account.id}/behdashtik_ai/health",
          headers: admin.create_new_auth_token
      expect(response).to have_http_status(:service_unavailable)
    end
  end
end
