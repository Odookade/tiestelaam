/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const SOURCE_META = {
    torob: { label: "ترب", icon: "fa-bolt" },
    digikala: { label: "دیجی‌کالا", icon: "fa-shopping-bag" },
    divar: { label: "دیوار", icon: "fa-map-marker" },
};

/**
 * نمایش خطوط price.inquiry.line به‌صورت کارت‌های مربعیِ عکس‌دار
 * (شبیه شبکه محصولات فروشگاهی) به‌جای لیست خام اودو.
 */
export class PriceInquiryCardsField extends Component {
    static template = "tiestelaam.PriceInquiryCardsField";
    static props = { ...standardFieldProps };

    get records() {
        return this.props.record.data[this.props.name].records;
    }

    formatPrice(value) {
        if (!value) {
            return "";
        }
        return new Intl.NumberFormat("fa-IR").format(Math.round(value));
    }

    sourceLabel(source) {
        return (SOURCE_META[source] && SOURCE_META[source].label) || source || "";
    }

    sourceIcon(source) {
        return (SOURCE_META[source] && SOURCE_META[source].icon) || "fa-tag";
    }

    openLink(rec) {
        const url = rec.data.url;
        if (url) {
            window.open(url, "_blank", "noopener");
        }
    }

    stopClick(ev) {
        ev.stopPropagation();
    }
}

registry.category("fields").add("price_inquiry_cards", {
    component: PriceInquiryCardsField,
    supportedTypes: ["one2many"],
});
