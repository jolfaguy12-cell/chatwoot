require 'rails_helper'

RSpec.describe Behdashtik::VisitorJourneyService do
  let(:account) { create(:account) }
  let(:conversation) { create(:conversation, account: account) }

  def service(params)
    described_class.new(conversation, ActionController::Parameters.new(params).permit!)
  end

  describe '#perform' do
    it 'creates a private note with the sanitized page visit' do
      expect do
        service(url: 'https://dev.behdashtik.ir/product/foo/?token=secret123&v=1',
                title: '<b>محصول</b> تست', timestamp: Time.current.iso8601).perform
      end.to change(conversation.messages.where(private: true), :count).by(1)

      note = conversation.messages.where(private: true).last
      expect(note.content).to include('https://dev.behdashtik.ir/product/foo/?v=1')
      expect(note.content).not_to include('secret123')
      expect(note.content).to include('محصول تست')
    end
  end

  describe '#update_page_context' do
    it 'merges page context into custom_attributes without clobbering existing keys' do
      conversation.update!(custom_attributes: { 'existing_key' => 'keep-me' })

      service(url: 'https://dev.behdashtik.ir/product/dragon-ranee-two-color-eyeshadow-stick/',
              title: 'سایه چشم', timestamp: Time.current.iso8601).update_page_context

      attrs = conversation.reload.custom_attributes
      expect(attrs['existing_key']).to eq('keep-me')
      expect(attrs['behdashtik_current_url'])
        .to eq('https://dev.behdashtik.ir/product/dragon-ranee-two-color-eyeshadow-stick/')
      expect(attrs['behdashtik_product_slug']).to eq('dragon-ranee-two-color-eyeshadow-stick')
      expect(attrs['behdashtik_page_title']).to eq('سایه چشم')
      expect(attrs['behdashtik_page_seen_at']).to be_present
    end

    it 'URL-decodes percent-encoded Persian product slugs' do
      service(url: 'https://dev.behdashtik.ir/product/%d8%ae%d8%b1%db%8c%d8%af/',
              title: '', timestamp: nil).update_page_context

      expect(conversation.reload.custom_attributes['behdashtik_product_slug']).to eq('خرید')
    end

    it 'stores an empty slug on non-product pages' do
      service(url: 'https://dev.behdashtik.ir/cart/', title: 'سبد خرید',
              timestamp: nil).update_page_context

      attrs = conversation.reload.custom_attributes
      expect(attrs['behdashtik_product_slug']).to eq('')
      expect(attrs['behdashtik_current_url']).to eq('https://dev.behdashtik.ir/cart/')
    end

    it 'does nothing when the url is blank' do
      expect do
        service(url: '', title: 'x', timestamp: nil).update_page_context
      end.not_to(change { conversation.reload.custom_attributes })
    end
  end
end
