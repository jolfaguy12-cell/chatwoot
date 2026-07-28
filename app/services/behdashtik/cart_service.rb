# Bridges the visitor's WooCommerce cart into the conversation.
#
# The cart lives in the shopper's own WooCommerce session, so only their browser
# can read or change it. The plugin does that and reports the result here, which
# (a) mirrors the cart into custom_attributes so the AI agent can talk about it,
# and (b) posts the success/error notice with cart and checkout buttons.
class Behdashtik::CartService
  MAX_ITEMS = 12
  MAX_TEXT = 300

  def initialize(conversation, params)
    @conversation = conversation
    @params = params
  end

  def perform
    update_cart_context
    post_feedback unless @params[:action_kind].to_s == 'sync'
  end

  private

  def cart
    @cart ||= (@params[:cart] || {}).to_h
  end

  def items
    @items ||= (cart['items'] || []).first(MAX_ITEMS).map do |item|
      {
        'key' => item['key'].to_s,
        'name' => sanitize(item['name']),
        'quantity' => item['quantity'].to_i,
        'price' => item['price'].to_i,
        'image' => item['image'].to_s,
        'variation' => sanitize(item['variation'])
      }
    end
  end

  def update_cart_context
    attributes = {
      'behdashtik_cart' => {
        'count' => cart['count'].to_i,
        'total' => cart['total'].to_i,
        'items' => items
      },
      'behdashtik_cart_updated_at' => Time.current.utc.strftime('%Y-%m-%d %H:%M:%S UTC')
    }
    @conversation.update!(custom_attributes: @conversation.custom_attributes.to_h.merge(attributes))
  rescue StandardError => e
    Rails.logger.error "[BehdashtikCart] Failed to update cart context: #{e.class}"
    nil
  end

  def post_feedback
    ::Messages::MessageBuilder.new(nil, @conversation, feedback_params).perform
  rescue StandardError => e
    Rails.logger.error "[BehdashtikCart] Failed to post feedback message: #{e.class}"
    nil
  end

  def feedback_params
    ActionController::Parameters.new(
      content: notice_text,
      content_type: 'cards',
      content_attributes: { items: [feedback_card] },
      sender_type: 'AgentBot',
      sender_id: @conversation.inbox.agent_bot&.id
    ).permit!
  end

  def succeeded?
    ActiveModel::Type::Boolean.new.cast(@params[:ok])
  end

  def notice_text
    return sanitize(@params[:message]) if @params[:message].present?

    succeeded? ? 'سبد خرید شما به‌روز شد.' : 'انجام نشد.'
  end

  def feedback_card
    {
      title: succeeded? ? '✅ سبد خرید به‌روز شد' : '⚠️ انجام نشد',
      description: card_description,
      media_url: @params[:product_image].to_s,
      actions: [
        { type: 'postback', text: 'مشاهده سبد خرید', payload: 'open_cart' },
        { type: 'postback', text: 'تسویه حساب', payload: 'open_checkout' }
      ]
    }
  end

  # The widget renders only the card for a `cards` message, so the notice text
  # has to live in the card body — not just in the message content.
  def card_description
    parts = [notice_text]
    parts << sanitize(@params[:product_name]) if succeeded? && @params[:product_name].present?
    parts << "جمع سبد: #{fa_amount(cart['total'])} تومان (#{fa_digits(cart['count'].to_i)} کالا)" if cart['count'].to_i.positive?
    parts.join(' — ')
  end

  def fa_amount(value)
    fa_digits(value.to_i.to_s.reverse.scan(/\d{1,3}/).join('٬').reverse)
  end

  def fa_digits(value)
    value.to_s.tr('0123456789', '۰۱۲۳۴۵۶۷۸۹')
  end

  def sanitize(raw)
    return '' if raw.blank?

    CGI.unescapeHTML(raw.to_s).slice(0, MAX_TEXT).gsub(/<[^>]+>/, '').strip
  end
end
