class Api::V1::Behdashtik::CartEventsController < ApplicationController
  include Behdashtik::WidgetAuthenticatable

  prepend_before_action :check_feature_enabled

  def create
    conversation = find_active_conversation
    return head :accepted unless conversation

    Behdashtik::CartService.new(conversation, cart_params).perform
    head :no_content
  rescue StandardError => e
    Rails.logger.error "[BehdashtikCart] Error processing event: #{e.class}"
    head :unprocessable_entity
  end

  private

  def check_feature_enabled
    head :not_found unless ENV.fetch('BEHDASHTIK_CART_SYNC_ENABLED', 'false') == 'true'
  end

  # `action` is taken by Rails routing, hence action_kind. The cart arrives as a
  # JSON string inside a form-encoded body: that keeps the browser request
  # "simple" (no CORS preflight, which this API does not answer).
  def cart_params
    params.permit(:action_kind, :ok, :message, :product_name, :product_image, :cart)
          .to_h
          .merge('cart' => parsed_cart)
          .with_indifferent_access
  end

  def parsed_cart
    JSON.parse(params[:cart].to_s)
  rescue JSON::ParserError
    {}
  end
end
