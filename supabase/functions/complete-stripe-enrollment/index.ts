import { createClient } from 'jsr:@supabase/supabase-js@2';

type Body = {
  courseId: string;
  studentId: string;
  checkoutSessionId: string;
};

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const stripeSecretKey = Deno.env.get('STRIPE_SECRET_KEY') || '';
const supabaseUrl = Deno.env.get('SUPABASE_URL') || '';
const supabaseServiceRoleKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') || '';

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders,
      ...(init.headers || {}),
    },
  });
}

function assertEnv() {
  if (!stripeSecretKey) throw new Error('STRIPE_SECRET_KEY is not set.');
  if (!supabaseUrl) throw new Error('SUPABASE_URL is not set.');
  if (!supabaseServiceRoleKey) throw new Error('SUPABASE_SERVICE_ROLE_KEY is not set.');
}

async function stripeGet(path: string) {
  const response = await fetch(`https://api.stripe.com/v1/${path}`, {
    headers: {
      Authorization: `Bearer ${stripeSecretKey}`,
    },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.error?.message || 'Stripe request failed.');
  }
  return data;
}

function parseAmount(value: unknown) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n : 0;
}

async function handler(req: Request) {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  try {
    assertEnv();
    const body = (await req.json()) as Body;
    const courseId = body.courseId?.trim();
    const studentId = body.studentId?.trim();
    const checkoutSessionId = body.checkoutSessionId?.trim();

    if (!courseId || !studentId || !checkoutSessionId) {
      return jsonResponse(
        { error: 'courseId, studentId and checkoutSessionId are required.' },
        { status: 400 }
      );
    }

    const supabase = createClient(supabaseUrl, supabaseServiceRoleKey);
    const { data: existingEnrollment } = await supabase
      .from('enrollments')
      .select('id,courseId,studentId')
      .eq('courseId', courseId)
      .eq('studentId', studentId)
      .maybeSingle();

    const { data: existingInvoice } = await supabase
      .from('invoices')
      .select('id,studentId,courseId,enrollmentId,paymentId,invoiceStatus,invoiceMethod,invoiceGateway,transactionId,gatewayTransactionId,invoiceAmount,totalAmount,currencyType,isSuccessful,receiptUrl,invoiceCompletedAt')
      .eq('studentId', studentId)
      .eq('courseId', courseId)
      .eq('transactionId', checkoutSessionId)
      .maybeSingle();

    if (existingInvoice && existingEnrollment) {
      return jsonResponse({
        success: true,
        enrollment: existingEnrollment,
        payment: null,
        invoice: existingInvoice,
        checkoutSessionId,
        reused: true,
      });
    }

    const { data: course, error: courseError } = await supabase
      .from('courses')
      .select('id,title,price,pricingType,discountPrice')
      .eq('id', courseId)
      .single();

    if (courseError || !course) {
      return jsonResponse({ error: 'Course not found.' }, { status: 404 });
    }

    const checkoutSession = await stripeGet(`checkout/sessions/${checkoutSessionId}`);
    if (checkoutSession.payment_status !== 'paid' && checkoutSession.status !== 'complete') {
      return jsonResponse({ error: 'Checkout session is not complete.' }, { status: 400 });
    }

    const amount = parseAmount(course.price);
    const paymentIntentId =
      typeof checkoutSession.payment_intent === 'string'
        ? checkoutSession.payment_intent
        : checkoutSession.payment_intent?.id || null;
    const paymentIntent =
      paymentIntentId && typeof checkoutSession.payment_intent === 'string'
        ? await stripeGet(`payment_intents/${paymentIntentId}`)
        : checkoutSession.payment_intent;
    const chargeId = paymentIntent?.latest_charge || paymentIntent?.charges?.data?.[0]?.id || null;

    const paymentPayload = {
      studentId,
      courseId,
      amount,
      currency: checkoutSession.currency || 'usd',
      paymentMethod: 'stripe',
      stripePaymentIntentId: paymentIntentId,
      stripeChargeId: chargeId,
      status: 'completed',
      completedAt: new Date().toISOString(),
    };

    const { data: paymentUpsert, error: paymentError } = await supabase
      .from('payments')
      .upsert(paymentPayload, { onConflict: 'studentId,courseId' })
      .select()
      .single();

    if (paymentError) {
      return jsonResponse({ error: paymentError.message }, { status: 500 });
    }

    let enrollmentRecord = existingEnrollment;
    if (!enrollmentRecord) {
      const { data, error } = await supabase
        .from('enrollments')
        .insert([
          {
            courseId,
            studentId,
            status: 'ACTIVE',
            completed: false,
            progressPercentage: 0,
            completedLessons: 0,
            enrolledAt: new Date().toISOString(),
          },
        ])
        .select()
        .single();
      if (error) {
        return jsonResponse({ error: error.message }, { status: 500 });
      }
      enrollmentRecord = data;
    }

    const { data: invoiceData, error: invoiceError } = await supabase
      .from('invoices')
      .insert([
        {
          studentId,
          courseId,
          enrollmentId: enrollmentRecord.id,
          paymentId: paymentUpsert.id,
          invoiceType: 'course_enrollment',
          invoiceStatus: 'paid',
          invoiceMethod: 'stripe',
          invoiceGateway: 'stripe',
          transactionId: checkoutSession.id,
          gatewayTransactionId: paymentIntentId,
          invoiceAmount: amount,
          taxAmount: 0,
          totalAmount: amount,
          discountApplied: 0,
          currencyType: checkoutSession.currency || 'usd',
          isSuccessful: true,
          receiptUrl: checkoutSession.invoice || null,
          invoiceCompletedAt: new Date().toISOString(),
        },
      ])
      .select()
      .single();

    if (invoiceError) {
      return jsonResponse({ error: invoiceError.message }, { status: 500 });
    }

    return jsonResponse({
      success: true,
      enrollment: enrollmentRecord,
      payment: paymentUpsert,
      invoice: invoiceData,
      checkoutSessionId,
    });
  } catch (error) {
    return jsonResponse(
      { error: error instanceof Error ? error.message : 'Unknown error' },
      { status: 500 }
    );
  }
}

Deno.serve(handler);
