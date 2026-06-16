/** Smoke tests for Member Service. */
const request = require('supertest');
const { app, server } = require('../src/index');

afterAll((done) => {
    server.close(done);
});

describe('Health checks', () => {
    test('GET /healthz returns healthy', async () => {
        const res = await request(app).get('/healthz');
        expect(res.status).toBe(200);
        expect(res.body.service).toBe('member');
    });

    test('GET /readyz returns ready', async () => {
        const res = await request(app).get('/readyz');
        expect(res.status).toBe(200);
    });
});

describe('Member API', () => {
    test('GET /v1/members?customerId= creates new member', async () => {
        const res = await request(app).get('/v1/members?customerId=cust-test-001');
        expect(res.status).toBe(200);
        expect(res.body.member_id).toBeDefined();
        expect(res.body.level).toBe('GOLD');
    });

    test('GET /v1/members/:id returns member', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-002');
        const res = await request(app).get(`/v1/members/${create.body.member_id}`);
        expect(res.status).toBe(200);
        expect(res.body.member_no).toBeDefined();
    });

    test('GET /v1/members/:id/points returns points', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-003');
        const res = await request(app).get(`/v1/members/${create.body.member_id}/points`);
        expect(res.status).toBe(200);
        expect(res.body.balance).toBe(15800);
        expect(res.body.transactions.length).toBeGreaterThan(0);
    });

    test('POST /v1/members/:id/points/exchange deducts points', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-004');
        const memberId = create.body.member_id;
        const res = await request(app)
            .post(`/v1/members/${memberId}/points/exchange`)
            .send({ points: 500, reward_id: 'snack-001', reward_type: 'CAT_SNACK', quantity: 1 });
        expect(res.status).toBe(200);
        expect(res.body.points_deducted).toBe(500);
        expect(res.body.remaining_balance).toBe(15300);
    });

    test('POST exchange fails on insufficient points', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-005');
        const memberId = create.body.member_id;
        const res = await request(app)
            .post(`/v1/members/${memberId}/points/exchange`)
            .send({ points: 999999, reward_id: 'snack-001', reward_type: 'CAT_SNACK' });
        expect(res.status).toBe(422);
        expect(res.body.code).toBe('INSUFFICIENT_POINTS');
    });

    test('GET /v1/members/:id/coupons returns coupons', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-006');
        const res = await request(app).get(`/v1/members/${create.body.member_id}/coupons`);
        expect(res.status).toBe(200);
        expect(res.body.content.length).toBeGreaterThan(0);
        expect(res.body.total_available).toBe(2);
    });

    test('POST lock / redeem / unlock coupon lifecycle', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-007');
        const memberId = create.body.member_id;

        // Get first available coupon
        const couponsRes = await request(app).get(`/v1/members/${memberId}/coupons?status=AVAILABLE`);
        const couponId = couponsRes.body.content[0].coupon_id;

        // Lock
        const lockRes = await request(app)
            .post(`/v1/members/${memberId}/coupons/${couponId}/lock`)
            .send({ order_id: 'order-001' });
        expect(lockRes.status).toBe(200);
        expect(lockRes.body.status).toBe('LOCKED');

        // Redeem
        const redeemRes = await request(app)
            .post(`/v1/members/${memberId}/coupons/${couponId}/redeem`)
            .send({ order_id: 'order-001', order_amount: { amount: 30, currency: 'CNY' } });
        expect(redeemRes.status).toBe(200);
        expect(redeemRes.body.status).toBe('USED');
    });

    test('GET /v1/members/:id/history returns events', async () => {
        const create = await request(app).get('/v1/members?customerId=cust-test-008');
        const res = await request(app).get(`/v1/members/${create.body.member_id}/history`);
        expect(res.status).toBe(200);
        expect(res.body.content.length).toBeGreaterThan(0);
    });
});

describe('Error handling', () => {
    test('GET non-existent member returns 404', async () => {
        const res = await request(app).get('/v1/members/non-existent');
        expect(res.status).toBe(404);
    });

    test('POST exchange on non-existent member returns 404', async () => {
        const res = await request(app)
            .post('/v1/members/non-existent/points/exchange')
            .send({ points: 100, reward_id: 'x', reward_type: 'CAT_SNACK' });
        expect(res.status).toBe(404);
    });
});
