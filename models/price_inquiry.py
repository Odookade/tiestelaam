from odoo import models, fields, api
from odoo.exceptions import UserError
import requests
from bs4 import BeautifulSoup
import logging
import re
import json
import urllib.parse

_logger = logging.getLogger(__name__)


class PriceInquiryLine(models.Model):
    """خطوط محصولات مشابه"""
    _name = 'price.inquiry.line'
    _description = 'خطوط استعلام قیمت'
    _order = 'sequence asc'
    
    inquiry_id = fields.Many2one('price.inquiry', 'استعلام والد', required=True, ondelete='cascade')
    sequence = fields.Integer('ترتیب', default=0)
    name = fields.Char('نام محصول')
    price = fields.Float('قیمت')
    url = fields.Char('لینک')
    source = fields.Char('منبع')
    image_url = fields.Char('لینک تصویر')
    badge_text = fields.Char('برچسب')
    meta_text = fields.Char('اطلاعات تکمیلی')
    button_url = fields.Html('لینک محصول', compute='_compute_button_url', sanitize=False)

    def _compute_button_url(self):
        for rec in self:
            if rec.url:
                rec.button_url = f"""
                <a href="{rec.url}" target="_blank"
                style="
                background:#2563eb;
                color:white;
                padding:6px 14px;
                border-radius:8px;
                text-decoration:none;
                font-weight:600;
                ">
                🔎 باز کردن محصول
                </a>
                """
            else:
                rec.button_url = ""



class PriceInquiry(models.Model):
    _name = 'price.inquiry'
    _description = 'استعلام قیمت آنلاین'
    _order = 'create_date desc'

    name = fields.Char('نام کالا', required=True)
    product_id = fields.Many2one('product.product', 'محصول مرتبط')
    source = fields.Selection([
        ('torob', 'ترب'),
        ('digikala', 'دیجی‌کالا'),
        ('divar', 'دیوار'),
        ('all', 'همه (مقایسه سه‌گانه)'),
    ], 'منبع', required=True, default='digikala')
    price = fields.Float('قیمت استعلام شده')
    url = fields.Char('لینک محصول')
    inquiry_date = fields.Datetime('تاریخ استعلام', default=fields.Datetime.now)
    state = fields.Selection([
        ('draft', 'در انتظار'),
        ('done', 'انجام شده'),
        ('failed', 'ناموفق'),
    ], default='draft', string='وضعیت')
    error_message = fields.Text('پیام خطا')

    auto_refresh = fields.Boolean('استعلام روزانه خودکار', default=False)

    line_ids = fields.One2many('price.inquiry.line', 'inquiry_id', 'محصولات مشابه')
    line_count = fields.Integer('تعداد محصولات مشابه', compute='_compute_line_count', store=True)
    main_image_url = fields.Char('تصویر شاخص', compute='_compute_main_image_url')

    user_id = fields.Many2one(
        'res.users',
        string='کاربر',
        default=lambda self: self.env.user,
        readonly=True
    )

    @api.depends('line_ids')
    def _compute_line_count(self):
        for record in self:
            record.line_count = len(record.line_ids)

    @api.depends('line_ids.image_url')
    def _compute_main_image_url(self):
        for record in self:
            record.main_image_url = record.line_ids[:1].image_url or False

    def run_scheduled_inquiry(self):
        """توسط کرون روزانه اجرا می‌شه؛ فقط رکوردهایی که کاربر
        گزینه‌ی «استعلام روزانه خودکار» رو براشون فعال کرده دوباره
        استعلام می‌گیره."""
        records = self.search([('auto_refresh', '=', True)])
        for record in records:
            try:
                record.action_inquiry_price()
            except Exception as e:
                _logger.error(f'خطا در استعلام خودکار رکورد {record.id}: {e}')
        return True

    @api.onchange('source')
    def _onchange_source(self):
        for record in self:
            if record.state in ['done', 'failed']:
                record.state = 'draft'
                record.price = 0
                record.url = False
                record.error_message = False

    def action_inquiry_price(self):
        for record in self:
            try:
                record.line_ids.unlink()

                lines = []

                if record.source == 'all':
                    # استعلام هم‌زمان از هر سه پلتفرم؛ اگه یکی خطا داد
                    # بقیه رو متوقف نمی‌کنه، فقط خطاش رو جمع می‌کنیم
                    errors = []
                    best_price = 0
                    best_url = ''

                    for src, getter in (
                        ('torob', self._get_torob_price),
                        ('digikala', self._get_digikala_price),
                        ('divar', self._get_divar_price),
                    ):
                        try:
                            res = getter(record.name)
                            for idx, product in enumerate(res.get('products', [])[:10]):
                                lines.append((0, 0, {
                                    'sequence': idx + 1,
                                    'name': product.get('name', ''),
                                    'price': product.get('price', 0),
                                    'url': product.get('url', ''),
                                    'source': src,
                                    'image_url': product.get('image_url', ''),
                                    'badge_text': product.get('badge_text', ''),
                                    'meta_text': product.get('meta_text', ''),
                                }))
                            price_candidates = [p.get('price') for p in res.get('products', []) if p.get('price')]
                            if price_candidates:
                                lowest = min(price_candidates)
                                if not best_price or lowest < best_price:
                                    best_price = lowest
                                    best_url = res.get('url', '')
                        except Exception as src_err:
                            errors.append(f'{src}: {src_err}')

                    if not lines:
                        raise Exception(' | '.join(errors) or 'هیچ نتیجه‌ای از هیچ‌کدام از پلتفرم‌ها یافت نشد')

                    record.write({
                        'price': best_price,
                        'url': best_url,
                        'line_ids': lines,
                        'state': 'done',
                        'error_message': ' | '.join(errors) if errors else False,
                        'inquiry_date': fields.Datetime.now()
                    })
                    continue

                if record.source == 'torob':
                    result = self._get_torob_price(record.name)
                elif record.source == 'digikala':
                    result = self._get_digikala_price(record.name)
                elif record.source == 'divar':
                    result = self._get_divar_price(record.name)
                else:
                    raise UserError('منبع پشتیبانی نشده')
                
                for idx, product in enumerate(result.get('products', [])):
                    lines.append((0, 0, {
                        'sequence': idx + 1,
                        'name': product.get('name', ''),
                        'price': product.get('price', 0),
                        'url': product.get('url', ''),
                        'source': record.source,
                        'image_url': product.get('image_url', ''),
                        'badge_text': product.get('badge_text', ''),
                        'meta_text': product.get('meta_text', ''),
                    }))
                
                record.write({
                    'price': result.get('price', 0),
                    'url': result.get('url', ''),
                    'line_ids': lines,
                    'state': 'done',
                    'error_message': False,
                    'inquiry_date': fields.Datetime.now()
                })
                
            except Exception as e:
                _logger.error(f'خطا در استعلام قیمت: {str(e)}')
                record.write({
                    'state': 'failed',
                    'error_message': str(e),
                    'inquiry_date': fields.Datetime.now()
                })

    def _convert_persian_arabic_numbers(self, text):
        if not text:
            return text
        
        conversion_map = {
            '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
            '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
            '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
            '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
        }
        
        result = text
        for old, new in conversion_map.items():
            result = result.replace(old, new)
        
        return result

    def _extract_price_from_text(self, text):
        if not text:
            return 0
        
        text = self._convert_persian_arabic_numbers(text)
        numbers = re.findall(r'[\d]+', text)
        
        if not numbers:
            return 0
        
        number_str = ''.join(numbers)
        
        try:
            return float(number_str)
        except:
            return 0

    def _get_torob_price(self, product_name):
        """دریافت قیمت از ترب"""
        _logger.info(f'شروع استعلام قیمت از ترب برای: {product_name}')

        encoded_query = urllib.parse.quote(product_name)
        search_url = f"https://torob.com/search/?query={encoded_query}&_search_landing=header"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://torob.com/',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'sec-ch-ua': '"Chromium";v="120", "Not_A Brand";v="24", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }

        try:
            session = requests.Session()
            # اول یه بار صفحه اصلی رو می‌گیریم تا کوکی‌های ضدربات ترب ست بشن،
            # دقیقاً مثل رفتار یک مرورگر واقعی که قبل از جستجو صفحه اصلی رو باز کرده
            warmup_status = None
            try:
                warmup_resp = session.get('https://torob.com/', headers=headers, timeout=10)
                warmup_status = warmup_resp.status_code
                _logger.info(f'کد پاسخ صفحه اصلی ترب (warm-up): {warmup_status}')
            except Exception as warmup_err:
                _logger.warning(f'خطا در warm-up ترب: {warmup_err}')

            response = session.get(search_url, headers=headers, timeout=20)
            _logger.info(f'کد پاسخ ترب: {response.status_code}')

            if response.status_code != 200:
                # بخشی از بدنه پاسخ رو هم لاگ/گزارش می‌کنیم چون کد خطاهای
                # غیراستاندارد (مثل 490) معمولاً از فایروال/آنتی‌بات ترب میان.
                # اگه بدنه خالی بود (که معمولاً یعنی بلاک شبکه‌ای/فایروال، نه
                # صفحه‌ی کپچا)، به‌جاش چند هدر کلیدی پاسخ رو نشون می‌دیم.
                body_snippet = (response.text or '')[:300].replace('\n', ' ').strip()
                header_hints = {
                    k: v for k, v in response.headers.items()
                    if k.lower() in ('server', 'content-length', 'content-type', 'cf-ray', 'x-request-id', 'via')
                }
                _logger.warning(f'بدنه پاسخ ترب: {body_snippet} | هدرها: {header_hints}')

                if body_snippet:
                    extra = f' - پاسخ سرور: {body_snippet}'
                elif header_hints:
                    extra = f' - بدنه خالی بود، هدرها: {header_hints}'
                else:
                    extra = ' - بدنه و هدر مفیدی برنگشت (احتمالاً بلاک در سطح شبکه/فایروال)'

                if warmup_status and warmup_status != 200:
                    extra += f' | حتی صفحه اصلی ترب هم {warmup_status} داد، یعنی IP سرور شما مسدوده'

                raise Exception(f'خطای HTTP: {response.status_code}{extra}')
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            products_list = []
            
            # روش 1: یافتن تمام لینک‌های محصول و استخراج اطلاعات
            product_links = soup.find_all('a', href=re.compile(r'/p/[a-zA-Z0-9-]+/'))
            
            _logger.info(f'تعداد لینک‌های محصول یافت شده: {len(product_links)}')
            
            seen_urls = set()
            
            for link in product_links:
                try:
                    href = link.get('href', '')
                    
                    # جلوگیری از تکرار
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    
                    # ساخت URL کامل
                    url = 'https://torob.com' + href if href.startswith('/') else href
                    
                    # پیدا کردن نام - از تگ‌های h2, h3, h4 در والدین
                    name = ''
                    price = 0
                    image_url = ''
                    
                    # جستجو در والدین لینک
                    parent = link.parent
                    for level in range(20):
                        if not parent:
                            break
                        
                        # استخراج نام از h2, h3, h4
                        if not name or len(name) < 5:
                            for tag in ['h2', 'h3', 'h4', 'span', 'div']:
                                name_elem = parent.find(tag, class_=re.compile(r'name|title', re.I))
                                if name_elem:
                                    name = name_elem.get_text(strip=True)
                                    break
                        
                        # استخراج قیمت از کلاس مشخص
                        if price == 0:
                            price_elem = parent.find('div', class_=re.compile(r'price-text|price', re.I))
                            if price_elem:
                                price_text = price_elem.get_text(strip=True)
                                price = self._extract_price_from_text(price_text)
                        
                        # اگر قیمت پیدا نشد، از کل متن بگیر
                        if price == 0:
                            text = parent.get_text(strip=True)
                            price = self._extract_price_from_text(text)
                        
                        # استخراج تصویر محصول
                        if not image_url:
                            img_elem = parent.find('img')
                            if img_elem:
                                image_url = img_elem.get('src') or img_elem.get('data-src') or ''
                                if image_url.startswith('//'):
                                    image_url = 'https:' + image_url
                        
                        parent = parent.parent
                    
                    # اگر نام پیدا نشد، از خود لینک بگیر
                    if not name or len(name) < 5:
                        name = link.get_text(strip=True)
                    
                    # بررسی منطقی بودن
                    if name and len(name) > 3:
                        if price > 0:
                            products_list.append({
                                'name': name[:150],
                                'price': price,
                                'url': url,
                                'image_url': image_url,
                            })
                            _logger.info(f'محصول: {name[:50]} - {price} - {url}')
                        else:
                            # اگر قیمت نداشت، بعداً از صفحه محصول بگیر
                            products_list.append({
                                'name': name[:150],
                                'price': 0,
                                'url': url,
                                'image_url': image_url,
                            })
                    
                    if len(products_list) >= 20:
                        break
                        
                except Exception as e:
                    _logger.warning(f'خطا در پردازش لینک: {e}')
                    continue
            
            # روش 2: اگر لیست خالی یا کم بود
            if len(products_list) < 5:
                _logger.info('روش 1 کافی نبود، تلاش با روش 2')
                products_list = self._get_torob_fallback(soup, search_url)
            
            if not products_list:
                raise Exception('محصولی در ترب یافت نشد')
            
            # حذف موارد بدون قیمت
            products_with_price = [p for p in products_list if p['price'] > 0]
            if products_with_price:
                products_list = products_with_price
            
            main_price = products_list[0].get('price', 0)
            main_url = products_list[0].get('url', '')
            
            _logger.info(f'تعداد نهایی محصولات: {len(products_list)}')
            
            return {
                'price': main_price,
                'url': main_url,
                'products': products_list
            }
            
        except Exception as e:
            _logger.error(f'خطا در ترب: {e}')
            raise Exception(f'خطا در دریافت قیمت از ترب: {str(e)}')

    def _get_torob_fallback(self, soup, search_url):
        """روش جایگزین برای ترب"""
        _logger.info('استفاده از روش جایگزین')
        
        products_list = []
        seen_urls = set()
        
        # یافتن تمام لینک‌ها
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            href = link.get('href', '')
            
            # فقط لینک‌های محصول
            if '/p/' not in href:
                continue
            
            if href in seen_urls:
                continue
            seen_urls.add(href)
            
            url = 'https://torob.com' + href if href.startswith('/') else href
            
            name = ''
            price = 0
            image_url = ''
            
            # جستجو در والدین
            parent = link
            for _ in range(20):
                parent = parent.parent
                if not parent:
                    break
                
                text = parent.get_text(strip=True)
                
                if price == 0:
                    price = self._extract_price_from_text(text)
                
                if not name or len(name) < 5:
                    for tag in ['h2', 'h3', 'h4']:
                        elem = parent.find(tag)
                        if elem:
                            name = elem.get_text(strip=True)
                            break
                
                if not image_url:
                    img_elem = parent.find('img')
                    if img_elem:
                        image_url = img_elem.get('src') or img_elem.get('data-src') or ''
                        if image_url.startswith('//'):
                            image_url = 'https:' + image_url
            
            if 50000 < price < 500000000 and name and len(name) > 3:
                products_list.append({
                    'name': name[:150],
                    'price': price,
                    'url': url,
                    'image_url': image_url,
                })
                
                if len(products_list) >= 20:
                    break
        
        return products_list

    def _get_digikala_price(self, product_name):
        """دریافت قیمت از دیجی‌کالا"""
        _logger.info(f'شروع استعلام قیمت از دیجی‌کالا برای: {product_name}')
        
        search_url = "https://api.digikala.com/v1/search/"
        
        params = {
            'q': product_name,
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'fa-IR,fa;q=0.9,en;q=0.8',
        }
        
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            _logger.info(f'کد پاسخ دیجی‌کالا: {response.status_code}')
            
            if response.status_code != 200:
                raise Exception(f'خطای HTTP: {response.status_code}')
            
            data = response.json()
            
            products_list = []
            main_price = 0
            main_url = ''
            
            if data.get('status') == 200:
                products = data.get('data', {}).get('products', [])
                
                for idx, product in enumerate(products[:20]):
                    price = 0
                    if 'default_variant' in product:
                        variant = product['default_variant']
                        if 'price' in variant:
                            price = variant['price'].get('selling_price', 0)
                            price = price / 10
                    
                    product_id = product.get('id')
                    url = f"https://www.digikala.com/product/{product_id}/"
                    name = product.get('title_fa') or product.get('title', '')
                    
                    # عکس اصلی محصول
                    image_url = ''
                    images = product.get('images') or {}
                    main_image = images.get('main') or {}
                    image_urls = main_image.get('url') or []
                    if image_urls:
                        image_url = image_urls[0]
                    
                    # امتیاز کاربران به عنوان برچسب
                    badge_text = ''
                    rating = product.get('rating') or {}
                    rate = rating.get('rate')
                    rate_count = rating.get('count')
                    if rate:
                        badge_text = f"⭐ {rate}"
                        if rate_count:
                            badge_text += f" ({rate_count})"
                    
                    # نام برند به عنوان اطلاعات تکمیلی
                    meta_text = ''
                    brand = product.get('brand') or {}
                    if brand.get('title_fa'):
                        meta_text = brand.get('title_fa')
                    
                    products_list.append({
                        'name': name,
                        'price': price,
                        'url': url,
                        'image_url': image_url,
                        'badge_text': badge_text,
                        'meta_text': meta_text,
                    })
                    
                    if idx == 0:
                        main_price = price
                        main_url = url
            
            if not products_list:
                raise Exception('محصولی یافت نشد')
            
            return {
                'price': main_price,
                'url': main_url,
                'products': products_list
            }
            
        except Exception as e:
            _logger.error(f'خطا در دیجی‌کالا: {e}')
            raise Exception(f'خطا در دریافت قیمت از دیجی‌کالا: {str(e)}')


    def _get_divar_price(self, product_name):
        """دریافت آگهی‌های مشابه از دیوار

        دیوار API عمومی و مستند مثل دیجی‌کالا نداره؛ این متد از همون endpoint
        داخلی‌ای استفاده می‌کنه که خود سایت divar.ir برای جستجو صدا می‌زنه
        (با بررسی مستقیم درخواست واقعی مرورگر ساخته شده). چون این endpoint
        رسمی نیست، ممکنه دیوار ساختار پاسخش رو در آینده تغییر بده و این
        متد نیاز به به‌روزرسانی داشته باشه.
        """
        _logger.info(f'شروع استعلام قیمت از دیوار برای: {product_name}')

        search_url = "https://api.divar.ir/v8/postlist/w/search"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'fa-IR,fa;q=0.9,en;q=0.8',
            'Origin': 'https://divar.ir',
            'Referer': 'https://divar.ir/',
        }

        # ساختار زیر عیناً از payload واقعی جستجوی divar.ir گرفته شده.
        # category عمداً خالی گذاشته شده تا جستجو محدود به یک دسته نشه؛
        # city_ids هم خالیه تا (طبق مستندات غیررسمی) جستجو سراسر ایران باشه.
        payload = {
            "source_view": "SEARCH",
            "disable_recommendation": False,
            "search_data": {
                "form_data": {
                    "data": {}
                },
                "server_payload": {
                    "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                    "additional_form_data": {
                        "data": {
                            "sort": {"str": {"value": "recommended"}}
                        }
                    }
                },
                "query": product_name
            },
            "current_tab_slug": "default",
            "city_ids": [],
            "previous_place_ids": [],
        }

        try:
            response = requests.post(search_url, json=payload, headers=headers, timeout=20)
            _logger.info(f'کد پاسخ دیوار: {response.status_code}')

            if response.status_code != 200:
                raise Exception(f'خطای HTTP: {response.status_code}')

            data = response.json()

            widgets = self._extract_divar_widgets(data)

            products_list = []
            seen_tokens = set()

            for widget in widgets:
                try:
                    if not isinstance(widget, dict):
                        continue

                    # فقط ردیف‌های آگهی؛ ویجت‌های دیگه مثل تیتر لیست رو رد کن
                    if widget.get('widget_type') != 'POST_ROW':
                        continue

                    widget_data = widget.get('data') or {}
                    if not isinstance(widget_data, dict):
                        continue

                    title = widget_data.get('title')
                    if not title:
                        continue

                    action = widget_data.get('action') or {}
                    payload_data = action.get('payload') or {}
                    token = payload_data.get('token') or widget_data.get('token')

                    if not token:
                        continue

                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)

                    url = f'https://divar.ir/v/{token}'

                    # قیمت معمولاً توی middle_description_text میاد
                    # (مثل "۲۰,۰۰۰,۰۰۰ تومان")؛ فیلدهای top/bottom معمولاً
                    # وضعیت کالا یا محله‌ان و عمداً برای قیمت استفاده نمی‌شن.
                    price_text = widget_data.get('middle_description_text') or ''
                    price = self._extract_price_from_text(price_text)

                    # عکس آگهی
                    image_url = widget_data.get('image_url') or ''

                    # وضعیت کالا (مثل "در حد نو") یا برچسب قرمز (مثل "پله شده")
                    badge_text = (
                        widget_data.get('top_description_text')
                        or widget_data.get('red_text')
                        or ''
                    )

                    # محل آگهی (شهر و محله)
                    web_info = payload_data.get('web_info') or {}
                    location_parts = [
                        web_info.get('city_persian'),
                        web_info.get('district_persian'),
                    ]
                    meta_text = '، '.join([p for p in location_parts if p])

                    products_list.append({
                        'name': title[:150],
                        'price': price,
                        'url': url,
                        'image_url': image_url,
                        'badge_text': badge_text,
                        'meta_text': meta_text,
                    })

                    if len(products_list) >= 20:
                        break

                except Exception as e:
                    _logger.warning(f'خطا در پردازش آگهی دیوار: {e}')
                    continue

            if not products_list:
                raise Exception('آگهی‌ای در دیوار یافت نشد')

            # اولویت با آگهی‌هایی که قیمت عددی دارن (بعضی آگهی‌ها "توافقی" هستن)
            products_with_price = [p for p in products_list if p['price'] > 0]
            if products_with_price:
                products_list = products_with_price

            main_price = products_list[0].get('price', 0)
            main_url = products_list[0].get('url', '')

            _logger.info(f'تعداد نهایی آگهی‌های دیوار: {len(products_list)}')

            return {
                'price': main_price,
                'url': main_url,
                'products': products_list
            }

        except Exception as e:
            _logger.error(f'خطا در دیوار: {e}')
            raise Exception(f'خطا در دریافت آگهی از دیوار: {str(e)}')

    def _extract_divar_widgets(self, data):
        """پیدا کردن لیست widget های آگهی در پاسخ دیوار

        ساختار دقیق JSON دیوار مستند نیست و ممکنه بین نسخه‌ها فرق کنه،
        به همین دلیل چند کلید محتمل رو چک می‌کنیم.
        """
        if not isinstance(data, dict):
            return []

        for key in ('list_widgets', 'web_widgets', 'widget_list'):
            widgets = data.get(key)
            if isinstance(widgets, list) and widgets:
                return widgets

        # حالت تودرتو: list_data.widget_list
        list_data = data.get('list_data')
        if isinstance(list_data, dict):
            widgets = list_data.get('widget_list')
            if isinstance(widgets, list) and widgets:
                return widgets

        return []

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'محصولات مشابه',
            'res_model': 'price.inquiry.line',
            'view_mode': 'list,form',
            'domain': [('inquiry_id', '=', self.id)],
        }

    def action_set_product(self, product_id):
        """اتصال یا قطع اتصال این استعلام به یک محصول انبار.
        product_id=False یعنی لینک قطع بشه."""
        self.ensure_one()
        self.write({'product_id': product_id or False})
        return True

    def action_apply_price(self, target='sale', price=None):
        """قیمت استعلام‌شده رو روی قیمت فروش یا خرید محصولِ لینک‌شده می‌نشونه.

        target: 'sale' -> list_price (قیمت فروش)
                'cost'  -> standard_price (قیمت خرید/بهای تمام‌شده)
        price: اگه داده نشه، از self.price استفاده می‌شه (برای مقایسه سه‌گانه
               که چند قیمت داریم، از سمت کلاینت قیمت انتخاب‌شده پاس داده می‌شه)
        """
        self.ensure_one()
        if not self.product_id:
            raise UserError('اول باید این استعلام رو به یک محصول انبار وصل کنی.')

        apply_price = price if price is not None else self.price
        if not apply_price:
            raise UserError('قیمتی برای اعمال کردن وجود نداره.')

        template = self.product_id.product_tmpl_id
        if target == 'cost':
            template.write({'standard_price': apply_price})
        else:
            template.write({'list_price': apply_price})

        template.write({
            'last_inquiry_price': apply_price,
            'last_inquiry_date': fields.Datetime.now(),
        })
        return True


class PriceInquiryCart(models.Model):
    _name = 'price.inquiry.cart'
    _description = 'سبد خرید استعلام قیمت'
    _order = 'create_date desc'

    name = fields.Char('نام سبد', required=True, default='سبد خرید جدید')
    user_id = fields.Many2one(
        'res.users',
        string='کاربر',
        default=lambda self: self.env.user,
        readonly=True
    )

    line_ids = fields.One2many('price.inquiry.cart.line', 'cart_id', 'اقلام سبد')
    line_count = fields.Integer('تعداد اقلام', compute='_compute_totals', store=True)
    total_price = fields.Float('جمع کل', compute='_compute_totals', store=True)
    main_image_url = fields.Char('تصویر شاخص', compute='_compute_main_image_url')

    @api.depends('line_ids.subtotal')
    def _compute_totals(self):
        for cart in self:
            cart.line_count = len(cart.line_ids)
            cart.total_price = sum(cart.line_ids.mapped('subtotal'))

    @api.depends('line_ids.image_url')
    def _compute_main_image_url(self):
        for cart in self:
            cart.main_image_url = cart.line_ids[:1].image_url or False

    def action_print_invoice(self):
        self.ensure_one()
        return self.env.ref('tiestelaam.action_report_cart_invoice').report_action(self)


class PriceInquiryCartLine(models.Model):
    _name = 'price.inquiry.cart.line'
    _description = 'قلم سبد خرید'
    _order = 'sequence, id'

    sequence = fields.Integer('ترتیب', default=10)
    cart_id = fields.Many2one('price.inquiry.cart', 'سبد خرید', required=True, ondelete='cascade')

    name = fields.Char('نام محصول', required=True)
    price = fields.Float('قیمت واحد')
    quantity = fields.Integer('تعداد', default=1)
    subtotal = fields.Float('جمع', compute='_compute_subtotal', store=True)

    url = fields.Char('لینک محصول')
    source = fields.Selection([
        ('torob', 'ترب'),
        ('digikala', 'دیجی‌کالا'),
        ('divar', 'دیوار'),
    ], 'منبع')
    image_url = fields.Char('تصویر')
    meta_text = fields.Char('اطلاعات تکمیلی')

    @api.depends('price', 'quantity')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (line.price or 0) * (line.quantity or 0)
        


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    last_inquiry_price = fields.Float('آخرین قیمت استعلام شده')
    last_inquiry_date = fields.Datetime('تاریخ آخرین استعلام')