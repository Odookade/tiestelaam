from odoo import http
from odoo.http import request, Response
import json
import logging

_logger = logging.getLogger(__name__)


class PriceInquiryController(http.Controller):
    
    @http.route('/api/price-inquiry', type='json', auth='public', methods=['POST'])
    def price_inquiry_api(self, **kwargs):
        """
        API برای استعلام قیمت از خارج از Odoo
        """
        product_name = kwargs.get('product_name')
        source = kwargs.get('source', 'digikala')
        
        if not product_name:
            return {
                'success': False,
                'error': 'نام محصول الزامی است'
            }
        
        # ایجاد رکورد استعلام
        inquiry = request.env['price.inquiry'].sudo().create({
            'name': product_name,
            'source': source,
        })
        
        # اجرای استعلام
        inquiry.action_inquiry_price()
        
        return {
            'success': True,
            'data': {
                'id': inquiry.id,
                'name': inquiry.name,
                'price': inquiry.price,
                'source': inquiry.source,
                'state': inquiry.state,
                'inquiry_date': inquiry.inquiry_date.isoformat() if inquiry.inquiry_date else None,
            }
        }
    
    @http.route('/api/price-inquiry/<int:inquiry_id>', type='json', auth='public', methods=['GET'])
    def get_inquiry(self, inquiry_id):
        """
        دریافت اطلاعات یک استعلام خاص
        """
        inquiry = request.env['price.inquiry'].sudo().browse(inquiry_id)
        
        if not inquiry.exists():
            return {
                'success': False,
                'error': 'استعلام یافت نشد'
            }
        
        return {
            'success': True,
            'data': {
                'id': inquiry.id,
                'name': inquiry.name,
                'price': inquiry.price,
                'source': inquiry.source,
                'state': inquiry.state,
                'inquiry_date': inquiry.inquiry_date.isoformat() if inquiry.inquiry_date else None,
            }
        }
    
    @http.route('/price-inquiry/webhook', type='http', auth='public', methods=['POST'])
    def webhook_handler(self):
        """
        وب‌هوک برای دریافت نتایج از سیستم‌های خارجی
        """
        try:
            data = json.loads(request.httprequest.data)
            
            # پردازش داده‌های دریافتی
            product_name = data.get('product_name')
            price = data.get('price')
            source = data.get('source')
            
            if product_name and price:
                # جستجوی محصول مرتبط
                product = request.env['product.product'].sudo().search([
                    ('name', 'ilike', product_name)
                ], limit=1)
                
                inquiry = request.env['price.inquiry'].sudo().create({
                    'name': product_name,
                    'product_id': product.id if product else False,
                    'source': source,
                    'price': price,
                    'state': 'done',
                })
                
                return Response(
                    json.dumps({'success': True, 'inquiry_id': inquiry.id}),
                    status=200,
                    mimetype='application/json'
                )
            
            return Response(
                json.dumps({'success': False, 'error': 'داده ناقص'}),
                status=400,
                mimetype='application/json'
            )
            
        except Exception as e:
            _logger.error(f'خطا در وب‌هوک: {e}')
            return Response(
                json.dumps({'success': False, 'error': str(e)}),
                status=500,
                mimetype='application/json'
            )