class Api::V1::Behdashtik::JourneyEventsController < ApplicationController
  skip_before_action :verify_authenticity_token, raise: false
  before_action :check_feature_enabled
  before_action :validate_origin
  before_action :decode_auth_token
  before_action :find_web_widget
  before_action :validate_token_inbox_match
  before_action :find_contact_inbox

  def create
    conversation = find_active_conversation
    return head :accepted unless conversation

    service = Behdashtik::VisitorJourneyService.new(conversation, journey_params)
    service.update_page_context
    service.perform if private_notes_enabled?
    head :no_content
  rescue StandardError => e
    Rails.logger.error "[BehdashtikJourney] Error processing event: #{e.class}"
    head :unprocessable_entity
  end

  private

  def check_feature_enabled
    head :not_found unless ENV.fetch('BEHDASHTIK_VISITOR_JOURNEY_ENABLED', 'false') == 'true'
  end

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

  def private_notes_enabled?
    ENV.fetch('BEHDASHTIK_VISITOR_JOURNEY_PRIVATE_NOTES', 'true') == 'true'
  end

  def journey_params
    params.permit(:url, :title, :referrer_url, :timestamp)
  end
end
