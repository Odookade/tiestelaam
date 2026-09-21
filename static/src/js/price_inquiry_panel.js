/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const SOURCE_META = {
    torob: { label: "ترب", icon: "fa-bolt", cls: "o_tip_src_torob" },
    digikala: { label: "دیجی‌کالا", icon: "fa-shopping-bag", cls: "o_tip_src_digikala" },
    divar: { label: "دیوار", icon: "fa-map-marker", cls: "o_tip_src_divar" },
    all: { label: "مقایسه هر سه", icon: "fa-columns", cls: "o_tip_src_all" },
};

const FILTERS = [
    { key: "all", label: "همه" },
    { key: "draft", label: "در انتظار" },
    { key: "done", label: "موفق" },
    { key: "failed", label: "ناموفق" },
];

const INQUIRY_FIELDS = [
    "name", "source", "price", "state", "line_count",
    "main_image_url", "inquiry_date", "error_message", "auto_refresh", "product_id",
];

const LINE_FIELDS = [
    "sequence", "name", "price", "url", "source",
    "image_url", "badge_text", "meta_text",
];

/**
 * پنل کاملاً سفارشی استعلام قیمت — به‌جای تکیه بر ویوهای استاندارد
 * اودو (کانبان/فرم/لیست)، تمام فیلدها، دکمه‌ها و کارت‌ها اینجا
 * از صفر با HTML/CSS خودمون ساخته می‌شن.
 */
export class PriceInquiryPanel extends Component {
    static template = "tiestelaam.PriceInquiryPanel";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            inquiries: [],
            loading: true,
            filter: "all",
            query: "",
            runningIds: new Set(),
            deletingIds: new Set(),
            failedImages: new Set(),

            selectMode: false,
            selectedIds: new Set(),

            showCreate: false,
            creating: false,
            newName: "",
            newSource: "digikala",
            newAutoRefresh: false,

            detail: null,
            detailLoading: false,
            detailLines: [],

            productQuery: "",
            productResults: [],
            productSearching: false,
            linkingProduct: false,
            applyingPrice: false,

            showCartPicker: false,
            cartPickerLine: null,
            carts: [],
            cartsLoading: false,
            cartPickerNewName: "",
            addingToCart: false,
        });

        this.sources = Object.keys(SOURCE_META).map((key) => ({ key, ...SOURCE_META[key] }));
        this.filters = FILTERS;

        onWillStart(() => this.loadInquiries());
    }

    // ---------- helpers ----------

    sourceMeta(source) {
        return SOURCE_META[source] || { label: source || "", icon: "fa-tag", cls: "" };
    }

    formatPrice(value) {
        if (!value) {
            return "";
        }
        return new Intl.NumberFormat("fa-IR").format(Math.round(value));
    }

    formatDateTime(value) {
        if (!value) {
            return "";
        }
        // مقادیر datetime اودو به‌صورت UTC بدون timezone برمی‌گردن
        const date = new Date(value.replace(" ", "T") + "Z");
        if (isNaN(date.getTime())) {
            return "";
        }
        try {
            return new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
                dateStyle: "medium",
                timeStyle: "short",
            }).format(date);
        } catch (e) {
            return date.toLocaleString();
        }
    }

    stopClick(ev) {
        ev.stopPropagation();
    }

    onImageError(key) {
        this.state.failedImages.add(key);
    }

    imageOk(key, url) {
        return Boolean(url) && !this.state.failedImages.has(key);
    }

    get filteredInquiries() {
        const q = this.state.query.trim();
        return this.state.inquiries.filter((inq) => {
            if (this.state.filter !== "all" && inq.state !== this.state.filter) {
                return false;
            }
            if (q && !inq.name.includes(q)) {
                return false;
            }
            return true;
        });
    }

    setFilter(key) {
        this.state.filter = key;
    }

    // ---------- select mode / bulk delete ----------

    toggleSelectMode() {
        this.state.selectMode = !this.state.selectMode;
        this.state.selectedIds.clear();
    }

    isSelected(id) {
        return this.state.selectedIds.has(id);
    }

    toggleSelect(id, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (this.state.selectedIds.has(id)) {
            this.state.selectedIds.delete(id);
        } else {
            this.state.selectedIds.add(id);
        }
    }

    onCardClick(inq) {
        if (this.state.selectMode) {
            this.toggleSelect(inq.id);
        } else {
            this.openDetail(inq);
        }
    }

    async bulkDelete() {
        const ids = Array.from(this.state.selectedIds);
        if (!ids.length) {
            return;
        }
        if (!confirm(`${ids.length} استعلام حذف بشه؟`)) {
            return;
        }
        await this.orm.unlink("price.inquiry", ids);
        this.state.inquiries = this.state.inquiries.filter((i) => !this.state.selectedIds.has(i.id));
        this.state.selectedIds.clear();
        this.state.selectMode = false;
    }

    // ---------- data loading ----------

    async loadInquiries() {
        this.state.loading = true;
        try {
            const inquiries = await this.orm.searchRead(
                "price.inquiry",
                [],
                INQUIRY_FIELDS,
                { order: "create_date desc", limit: 200 }
            );
            this.state.inquiries = inquiries;
        } finally {
            this.state.loading = false;
        }
    }

    async refreshOne(id) {
        const [rec] = await this.orm.read("price.inquiry", [id], INQUIRY_FIELDS);
        const idx = this.state.inquiries.findIndex((i) => i.id === id);
        if (idx !== -1 && rec) {
            this.state.inquiries[idx] = rec;
        }
        if (this.state.detail && this.state.detail.id === id) {
            this.state.detail = rec;
        }
        return rec;
    }

    // ---------- create ----------

    openCreate() {
        this.state.newName = "";
        this.state.newSource = "digikala";
        this.state.newAutoRefresh = false;
        this.state.showCreate = true;
    }

    closeCreate() {
        if (this.state.creating) {
            return;
        }
        this.state.showCreate = false;
    }

    selectSource(key) {
        this.state.newSource = key;
    }

    toggleNewAutoRefresh() {
        this.state.newAutoRefresh = !this.state.newAutoRefresh;
    }

    async submitCreate() {
        const name = this.state.newName.trim();
        if (!name || this.state.creating) {
            return;
        }
        this.state.creating = true;
        try {
            const [id] = await this.orm.create("price.inquiry", [{
                name,
                source: this.state.newSource,
                auto_refresh: this.state.newAutoRefresh,
            }]);
            this.state.showCreate = false;
            await this.loadInquiries();
            this.runInquiry(id);
        } catch (err) {
            this.notification.add("ثبت استعلام ناموفق بود", { type: "danger" });
        } finally {
            this.state.creating = false;
        }
    }

    // ---------- actions on a card ----------

    async runInquiry(id, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (this.state.runningIds.has(id)) {
            return;
        }
        this.state.runningIds.add(id);
        try {
            await this.orm.call("price.inquiry", "action_inquiry_price", [[id]]);
            await this.refreshOne(id);
        } catch (err) {
            this.notification.add("استعلام قیمت با خطا مواجه شد", { type: "danger" });
        } finally {
            this.state.runningIds.delete(id);
        }
    }

    async toggleAutoRefresh(inq, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const newVal = !inq.auto_refresh;
        await this.orm.write("price.inquiry", [inq.id], { auto_refresh: newVal });
        inq.auto_refresh = newVal;
        if (this.state.detail && this.state.detail.id === inq.id) {
            this.state.detail.auto_refresh = newVal;
        }
    }

    async deleteInquiry(id, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!confirm("این استعلام حذف بشه؟")) {
            return;
        }
        this.state.deletingIds.add(id);
        try {
            await this.orm.unlink("price.inquiry", [id]);
            this.state.inquiries = this.state.inquiries.filter((i) => i.id !== id);
            if (this.state.detail && this.state.detail.id === id) {
                this.closeDetail();
            }
        } finally {
            this.state.deletingIds.delete(id);
        }
    }

    // ---------- detail overlay ----------

    async openDetail(inquiry) {
        this.state.detail = inquiry;
        this.state.detailLines = [];
        this.state.detailLoading = true;
        this.state.productQuery = "";
        this.state.productResults = [];
        try {
            const lines = await this.orm.searchRead(
                "price.inquiry.line",
                [["inquiry_id", "=", inquiry.id]],
                LINE_FIELDS,
                { order: "sequence asc" }
            );
            this.state.detailLines = lines;
        } finally {
            this.state.detailLoading = false;
        }
    }

    closeDetail() {
        this.state.detail = null;
        this.state.detailLines = [];
    }

    get isCompareMode() {
        return Boolean(this.state.detail) && this.state.detail.source === "all";
    }

    get compareColumns() {
        const keys = ["torob", "digikala", "divar"];
        return keys.map((key) => {
            const items = this.state.detailLines
                .filter((l) => l.source === key)
                .slice(0, 10);
            while (items.length < 10) {
                items.push(null);
            }
            return { key, meta: this.sourceMeta(key), items };
        });
    }

    openProductLink(url, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (url) {
            window.open(url, "_blank", "noopener");
        }
    }

    // ---------- اتصال به محصول انبار ----------

    async searchProducts() {
        const q = this.state.productQuery.trim();
        if (!q) {
            this.state.productResults = [];
            return;
        }
        this.state.productSearching = true;
        try {
            this.state.productResults = await this.orm.searchRead(
                "product.product",
                [["name", "ilike", q]],
                ["name", "default_code", "list_price"],
                { limit: 8 }
            );
        } finally {
            this.state.productSearching = false;
        }
    }

    onProductQueryInput(ev) {
        this.state.productQuery = ev.target.value;
        this.searchProducts();
    }

    async selectProduct(product) {
        if (this.state.linkingProduct) {
            return;
        }
        this.state.linkingProduct = true;
        try {
            await this.orm.call("price.inquiry", "action_set_product", [[this.state.detail.id], product.id]);
            this.state.detail.product_id = [product.id, product.name];
            this.state.productQuery = "";
            this.state.productResults = [];
            await this.refreshOne(this.state.detail.id);
        } finally {
            this.state.linkingProduct = false;
        }
    }

    async unlinkProduct() {
        if (this.state.linkingProduct) {
            return;
        }
        this.state.linkingProduct = true;
        try {
            await this.orm.call("price.inquiry", "action_set_product", [[this.state.detail.id], false]);
            this.state.detail.product_id = false;
        } finally {
            this.state.linkingProduct = false;
        }
    }

    async applyPrice(target, ev, priceOverride) {
        if (ev) {
            ev.stopPropagation();
        }
        if (this.state.applyingPrice || !this.state.detail || !this.state.detail.product_id) {
            return;
        }
        this.state.applyingPrice = true;
        try {
            await this.orm.call("price.inquiry", "action_apply_price", [[this.state.detail.id]], {
                target,
                price: priceOverride != null ? priceOverride : undefined,
            });
            this.notification.add(
                target === "cost" ? "قیمت خرید محصول به‌روزرسانی شد" : "قیمت فروش محصول به‌روزرسانی شد",
                { type: "success" }
            );
        } catch (err) {
            this.notification.add("اعمال قیمت ناموفق بود", { type: "danger" });
        } finally {
            this.state.applyingPrice = false;
        }
    }

    // ---------- افزودن به سبد خرید ----------

    async openCartPicker(line, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.state.cartPickerLine = line;
        this.state.cartPickerNewName = "";
        this.state.showCartPicker = true;
        this.state.cartsLoading = true;
        try {
            this.state.carts = await this.orm.searchRead(
                "price.inquiry.cart",
                [],
                ["name", "line_count"],
                { order: "create_date desc", limit: 50 }
            );
        } finally {
            this.state.cartsLoading = false;
        }
    }

    closeCartPicker() {
        if (this.state.addingToCart) {
            return;
        }
        this.state.showCartPicker = false;
        this.state.cartPickerLine = null;
    }

    async addLineToCart(cartId) {
        const line = this.state.cartPickerLine;
        if (!line || this.state.addingToCart) {
            return;
        }
        this.state.addingToCart = true;
        try {
            await this.orm.create("price.inquiry.cart.line", [{
                cart_id: cartId,
                name: line.name,
                price: line.price || 0,
                quantity: 1,
                url: line.url,
                source: line.source,
                image_url: line.image_url,
                meta_text: line.meta_text,
            }]);
            this.notification.add("به سبد خرید اضافه شد", { type: "success" });
            this.state.showCartPicker = false;
            this.state.cartPickerLine = null;
        } catch (err) {
            this.notification.add("افزودن به سبد خرید ناموفق بود", { type: "danger" });
        } finally {
            this.state.addingToCart = false;
        }
    }

    async createCartAndAdd() {
        const name = this.state.cartPickerNewName.trim();
        if (!name || this.state.addingToCart) {
            return;
        }
        this.state.addingToCart = true;
        try {
            const [cartId] = await this.orm.create("price.inquiry.cart", [{ name }]);
            this.state.addingToCart = false;
            await this.addLineToCart(cartId);
        } catch (err) {
            this.state.addingToCart = false;
            this.notification.add("ساخت سبد خرید ناموفق بود", { type: "danger" });
        }
    }
}

registry.category("actions").add("tiestelaam.panel", PriceInquiryPanel);
