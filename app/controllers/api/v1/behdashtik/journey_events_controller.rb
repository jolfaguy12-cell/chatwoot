class Api::V1::Behdashtik::JourneyEventsController < ApplicationController
  include Behdashtik::WidgetAuthenticatable

  prepend_before_action :check_feature_enabled

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

  def private_notes_enabled?
    ENV.fetch('BEHDASHTIK_VISITOR_JOURNEY_PRIVATE_NOTES', 'true') == 'true'
  end

  def journey_params
    params.permit(:url, :title, :referrer_url, :timestamp)
  end
end
