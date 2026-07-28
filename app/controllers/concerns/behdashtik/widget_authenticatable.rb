# Widget-JWT authentication for the Behdashtik browser modules (journey
# tracker, cart bridge). The visitor's own `cw_conversation` token is the proof
# of identity: it resolves to a contact_inbox, and from there to the
# conversation they are already chatting in. No agent or admin token involved.
module Behdashtik::WidgetAuthenticatable
  extend ActiveSupport::Concern

  included do
    skip_before_action :verify_authenticity_token, raise: false
    before_action :validate_origin
    before_action :decode_auth_token
    before_action :find_web_widget
    before_action :validate_token_inbox_match
    before_action :find_contact_inbox
  end

  private

  def validate_origin
    allowed_origins = ENV.fetch('BEHDASHTIK_VISITOR_JOURNEY_ALLOWED_ORIGINS', '').split(',').map(&:strip).reject(&:empty?)
    origin = request.headers['Origin'].to_s
    head :forbidden unless allowed_origins.any?(origin)
  end

  def decode_auth_token
    raw_token = params[:auth_token].to_s
    @jwt_payload = ::Widget::TokenService.new(token: raw_token).decode_token
    head :forbidden if @jwt_payload.blank?
  rescue StandardError
    head :forbidden
  end

  def find_web_widget
    @web_widget = ::Channel::WebWidget.find_by(website_token: params[:website_token])
    head :forbidden unless @web_widget
  end

  def validate_token_inbox_match
    inbox_id_from_jwt = @jwt_payload[:inbox_id]
    head :forbidden unless @web_widget.inbox.id == inbox_id_from_jwt
  end

  def find_contact_inbox
    @contact_inbox = @web_widget.inbox.contact_inboxes.find_by(source_id: @jwt_payload[:source_id])
    head :forbidden unless @contact_inbox
  end

  def find_active_conversation
    @contact_inbox.conversations
                  .where(status: %w[open pending])
                  .order(created_at: :desc)
                  .first
  end
end
