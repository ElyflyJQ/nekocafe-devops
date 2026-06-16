/**
 * NekoCafé Member Service — Express/Node.js implementation.
 * Matches D2-5 member-service.yaml OpenAPI contract.
 */
'use strict';

const express = require('express');
const { v4: uuidv4 } = require('uuid');

// === Observability: OpenTelemetry ===
const { trace, SpanStatusCode } = require('@opentelemetry/api');
const { NodeTracerProvider } = require('@opentelemetry/sdk-trace-node');
const { BatchSpanProcessor } = require('@opentelemetry/sdk-trace-base');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
const { ExpressInstrumentation } = require('@opentelemetry/instrumentation-express');
const { registerInstrumentations } = require('@opentelemetry/instrumentation');

const provider = new NodeTracerProvider();
provider.addSpanProcessor(new BatchSpanProcessor(new OTLPTraceExporter()));
provider.register();
registerInstrumentations({ instrumentations: [new ExpressInstrumentation()] });
const tracer = trace.getTracer('member-service');

// Structured JSON logging
const logger = {
    info(msg, extra = {}) {
        console.log(JSON.stringify({
            timestamp: new Date().toISOString(), level: 'INFO',
            service: 'member', message: msg, ...extra,
        }));
    },
    error(msg, extra = {}) {
        console.error(JSON.stringify({
            timestamp: new Date().toISOString(), level: 'ERROR',
            service: 'member', message: msg, ...extra,
        }));
    },
};

// === App ===
const app = express();
app.use(express.json());

app.use((req, res, next) => {
    const start = Date.now();
    res.on('finish', () => {
        logger.info(`${req.method} ${req.path} ${res.statusCode}`, {
            method: req.method, path: req.path,
            status: res.statusCode, duration_ms: Date.now() - start,
        });
    });
    next();
});

// === In-memory stores ===
const members = {};
const couponWallets = {};
const pointsTransactions = {};
const memberHistory = {};

function seedMember(customerId) {
    const memberId = `mem-${uuidv4().slice(0, 6)}`;
    const member = {
        member_id: memberId,
        customer_id: customerId,
        member_no: `NK${String(Date.now()).slice(-8)}`,
        level: 'GOLD',
        points_balance: 15800,
        total_points_earned: 32000,
        total_points_redeemed: 16200,
        level_valid_until: '2027-06-30',
        joined_at: '2025-03-15T10:00:00Z',
        last_activity_at: new Date().toISOString(),
    };
    members[memberId] = member;

    couponWallets[memberId] = [
        {
            coupon_id: `cpn-${uuidv4().slice(0, 6)}`,
            coupon_template_id: 'tmpl-001',
            name: '满200减30优惠券', type: 'DISCOUNT',
            discount_rule: { rule_type: 'AMOUNT_OFF', value: 30 },
            status: 'AVAILABLE',
            min_order_amount: { amount: 200, currency: 'CNY' },
            valid_from: '2026-06-01T00:00:00Z',
            valid_until: '2026-07-01T00:00:00Z',
            issued_at: '2026-06-01T00:00:00Z',
            used_at: null, used_on_order_id: null,
        },
        {
            coupon_id: `cpn-${uuidv4().slice(0, 6)}`,
            coupon_template_id: 'tmpl-002',
            name: '猫咪零食兑换券', type: 'FREE_ITEM',
            discount_rule: { rule_type: 'BUY_X_GET_Y', value: 1 },
            status: 'AVAILABLE',
            min_order_amount: { amount: 0, currency: 'CNY' },
            valid_from: '2026-06-01T00:00:00Z',
            valid_until: '2026-12-31T00:00:00Z',
            issued_at: '2026-06-01T00:00:00Z',
            used_at: null, used_on_order_id: null,
        },
    ];

    pointsTransactions[memberId] = [
        { transaction_id: `txn-${uuidv4().slice(0, 6)}`, type: 'EARNED', points: 240, balance_after: 15800, source: '订单消费 order-001', created_at: '2026-06-15T12:00:00Z' },
        { transaction_id: `txn-${uuidv4().slice(0, 6)}`, type: 'REDEEMED', points: -500, balance_after: 15560, source: '兑换猫咪零食 snack-001', created_at: '2026-06-10T18:00:00Z' },
    ];

    memberHistory[memberId] = [
        { event_id: `evt-${uuidv4().slice(0, 6)}`, event_type: 'COUPON_ISSUED', description: '系统发放满200减30优惠券', points_change: null, level_before: null, level_after: null, reference_id: `cpn-${uuidv4().slice(0, 6)}`, created_at: '2026-06-01T00:00:00Z' },
        { event_id: `evt-${uuidv4().slice(0, 6)}`, event_type: 'LEVEL_CHANGED', description: '累计消费达标，银卡升级为金卡', points_change: null, level_before: 'SILVER', level_after: 'GOLD', reference_id: null, created_at: '2025-12-01T00:00:00Z' },
    ];

    return member;
}

// === Health ===
app.get('/healthz', (req, res) => res.json({ status: 'healthy', service: 'member' }));
app.get('/readyz', (req, res) => res.json({ status: 'ready' }));

// === Members ===
app.get('/v1/members/:memberId', (req, res) => {
    const member = members[req.params.memberId];
    if (!member) return res.status(404).json({ code: 'NOT_FOUND', message: '会员不存在' });
    res.json(member);
});

app.get('/v1/members', (req, res) => {
    const { customerId } = req.query;
    const found = Object.values(members).find(m => m.customer_id === customerId);
    if (!found && customerId) {
        const member = seedMember(customerId);
        return res.json(member);
    }
    if (!found) return res.status(404).json({ code: 'NOT_FOUND', message: '会员不存在' });
    res.json(found);
});

// === Points ===
app.get('/v1/members/:memberId/points', (req, res) => {
    const member = members[req.params.memberId];
    if (!member) return res.status(404).json({ code: 'NOT_FOUND', message: '会员不存在' });
    const { page = 1, size = 20, type } = req.query;
    let txns = pointsTransactions[req.params.memberId] || [];
    if (type) txns = txns.filter(t => t.type === type);
    const start = (parseInt(page) - 1) * parseInt(size);
    res.json({
        member_id: member.member_id, balance: member.points_balance,
        total_earned: member.total_points_earned,
        total_redeemed: member.total_points_redeemed,
        expiring_next_month: 200,
        transactions: txns.slice(start, start + parseInt(size)),
    });
});

app.post('/v1/members/:memberId/points/exchange', (req, res) => {
    const member = members[req.params.memberId];
    if (!member) return res.status(404).json({ code: 'NOT_FOUND', message: '会员不存在' });
    const { points } = req.body;
    if (member.points_balance < points) {
        return res.status(422).json({ code: 'INSUFFICIENT_POINTS', message: '积分余额不足' });
    }
    member.points_balance -= points;
    member.total_points_redeemed += points;
    res.json({
        exchange_id: `exch-${uuidv4().slice(0, 6)}`,
        member_id: member.member_id, points_deducted: points,
        reward_name: `奖励 ${req.body.reward_id}`,
        remaining_balance: member.points_balance,
    });
});

// === Coupons ===
app.get('/v1/members/:memberId/coupons', (req, res) => {
    if (!members[req.params.memberId]) return res.status(404).json({ code: 'NOT_FOUND' });
    let coupons = couponWallets[req.params.memberId] || [];
    if (req.query.status) coupons = coupons.filter(c => c.status === req.query.status);
    res.json({ content: coupons, total_available: coupons.filter(c => c.status === 'AVAILABLE').length });
});

app.post('/v1/members/:memberId/coupons/:couponId/lock', (req, res) => {
    const coupons = couponWallets[req.params.memberId] || [];
    const coupon = coupons.find(c => c.coupon_id === req.params.couponId);
    if (!coupon) return res.status(404).json({ code: 'NOT_FOUND' });
    if (coupon.status !== 'AVAILABLE') return res.status(409).json({ code: 'COUPON_UNAVAILABLE', message: `状态为${coupon.status}，不可锁定` });
    coupon.status = 'LOCKED';
    coupon.locked_on_order_id = req.body.order_id;
    res.json({ coupon_id: coupon.coupon_id, status: 'LOCKED' });
});

app.post('/v1/members/:memberId/coupons/:couponId/redeem', (req, res) => {
    const coupons = couponWallets[req.params.memberId] || [];
    const coupon = coupons.find(c => c.coupon_id === req.params.couponId);
    if (!coupon) return res.status(404).json({ code: 'NOT_FOUND' });
    if (coupon.status !== 'LOCKED') return res.status(409).json({ code: 'COUPON_NOT_LOCKED' });
    coupon.status = 'USED';
    coupon.used_at = new Date().toISOString();
    coupon.used_on_order_id = req.body.order_id;
    res.json({ coupon_id: coupon.coupon_id, status: 'USED', discount_amount: req.body.order_amount || { amount: 30, currency: 'CNY' } });
});

app.post('/v1/members/:memberId/coupons/:couponId/unlock', (req, res) => {
    const coupons = couponWallets[req.params.memberId] || [];
    const coupon = coupons.find(c => c.coupon_id === req.params.couponId);
    if (!coupon) return res.status(404).json({ code: 'NOT_FOUND' });
    if (coupon.status !== 'LOCKED') return res.status(409).json({ code: 'COUPON_NOT_LOCKED' });
    coupon.status = 'AVAILABLE';
    coupon.locked_on_order_id = null;
    res.json({ coupon_id: coupon.coupon_id, status: 'AVAILABLE' });
});

// === History ===
app.get('/v1/members/:memberId/history', (req, res) => {
    if (!members[req.params.memberId]) return res.status(404).json({ code: 'NOT_FOUND' });
    const { event_type, page = 1, size = 20 } = req.query;
    let history = memberHistory[req.params.memberId] || [];
    if (event_type) history = history.filter(h => h.event_type === event_type);
    const start = (parseInt(page) - 1) * parseInt(size);
    res.json({ content: history.slice(start, start + parseInt(size)) });
});

// === Error handler ===
app.use((err, req, res, next) => {
    logger.error('Unhandled error', { error: err.message });
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'Internal server error' });
});

// === Start ===
const PORT = process.env.PORT || 8080;
const server = app.listen(PORT, () => {
    logger.info(`Member service started on port ${PORT}`);
});

module.exports = { app, server };
