/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const SOURCE_META = {
    torob: { label: "ترب", icon: "fa-bolt", cls: "o_tip_src_torob" },
    digikala: { label: "دیجی‌کالا", icon: "fa-shopping-bag", cls: "o_tip_src_digikala" },
    divar: { label: "دیوار", icon: "fa-map-marker", cls: "o_tip_src_divar" },
};

const CART_FIELDS = ["name", "line_count", "total_price", "main_image_url"];

const CART_LINE_FIELDS = [
    "sequence", "name", "price", "quantity", "subtotal",
    "url", "source", "image_url", "meta_text",
];

/**
 * پنل کاملاً سفارشی سبد خرید — هم‌راستا با پنل استعلام قیمت،
 * بدون تکیه بر ویوهای استاندارد اودو.
 */
export class PriceInquiryCartPanel extends Component {
    static template = "tiestelaam.PriceInquiryCartPanel";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            carts: [],
            loading: true,
            query: "",
            failedImages: new Set(),
            deletingIds: new Set(),

            showCreate: false,
            creating: false,
            newName: "",

            detail: null,
            detailLoading: false,
            detailLines: [],
            printing: false,
        });

        onWillStart(() => this.loadCarts());
    }

    // ---------- helpers ----------

    sourceMeta(source) {
        return SOURCE_META[source] || { label: source || "", icon: "fa-tag", cls: "" };
    }

    formatPrice(value) {
        if (!value) {
            return "۰";
        }
        return new Intl.NumberFormat("fa-IR").format(Math.round(value));
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

    get filteredCarts() {
        const q = this.state.query.trim();
        if (!q) {
            return this.state.carts;
        }
        return this.state.carts.filter((c) => c.name.includes(q));
    }

    // ---------- data loading ----------

    async loadCarts() {
        this.state.loading = true;
        try {
            this.state.carts = await this.orm.searchRead(
                "price.inquiry.cart",
                [],
                CART_FIELDS,
                { order: "create_date desc", limit: 200 }
            );
        } finally {
            this.state.loading = false;
        }
    }

    async refreshOne(id) {
        const [rec] = await this.orm.read("price.inquiry.cart", [id], CART_FIELDS);
        const idx = this.state.carts.findIndex((c) => c.id === id);
        if (idx !== -1 && rec) {
            this.state.carts[idx] = rec;
        }
        if (this.state.detail && this.state.detail.id === id) {
            this.state.detail = rec;
        }
        return rec;
    }

    // ---------- create ----------

    openCreate() {
        this.state.newName = "";
        this.state.showCreate = true;
    }

    closeCreate() {
        if (this.state.creating) {
            return;
        }
        this.state.showCreate = false;
    }

    async submitCreate() {
        const name = this.state.newName.trim();
        if (!name || this.state.creating) {
            return;
        }
        this.state.creating = true;
        try {
            await this.orm.create("price.inquiry.cart", [{ name }]);
            this.state.showCreate = false;
            await this.loadCarts();
        } catch (err) {
            this.notification.add("ساخت سبد خرید ناموفق بود", { type: "danger" });
        } finally {
            this.state.creating = false;
        }
    }

    async deleteCart(id, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!confirm("این سبد خرید حذف بشه؟")) {
            return;
        }
        this.state.deletingIds.add(id);
        try {
            await this.orm.unlink("price.inquiry.cart", [id]);
            this.state.carts = this.state.carts.filter((c) => c.id !== id);
            if (this.state.detail && this.state.detail.id === id) {
                this.closeDetail();
            }
        } finally {
            this.state.deletingIds.delete(id);
        }
    }

    // ---------- جزئیات سبد ----------

    async openDetail(cart) {
        this.state.detail = cart;
        this.state.detailLines = [];
        this.state.detailLoading = true;
        try {
            this.state.detailLines = await this.orm.searchRead(
                "price.inquiry.cart.line",
                [["cart_id", "=", cart.id]],
                CART_LINE_FIELDS,
                { order: "sequence asc" }
            );
        } finally {
            this.state.detailLoading = false;
        }
    }

    closeDetail() {
        this.state.detail = null;
        this.state.detailLines = [];
    }

    openProductLink(url, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (url) {
            window.open(url, "_blank", "noopener");
        }
    }

    async changeQuantity(line, delta, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const newQty = Math.max(1, (line.quantity || 1) + delta);
        if (newQty === line.quantity) {
            return;
        }
        line.quantity = newQty;
        line.subtotal = (line.price || 0) * newQty;
        await this.orm.write("price.inquiry.cart.line", [line.id], { quantity: newQty });
        await this.refreshOne(this.state.detail.id);
    }

    async removeLine(lineId, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        await this.orm.unlink("price.inquiry.cart.line", [lineId]);
        this.state.detailLines = this.state.detailLines.filter((l) => l.id !== lineId);
        await this.refreshOne(this.state.detail.id);
    }

    async printInvoice(cartId, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.state.printing = true;
        try {
            const action = await this.orm.call("price.inquiry.cart", "action_print_invoice", [[cartId]]);
            await this.actionService.doAction(action);
        } catch (err) {
            this.notification.add("چاپ فاکتور ناموفق بود", { type: "danger" });
        } finally {
            this.state.printing = false;
        }
    }
}

registry.category("actions").add("tiestelaam.cart_panel", PriceInquiryCartPanel);
