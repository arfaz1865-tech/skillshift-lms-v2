import { createClient } from 'jsr:@supabase/supabase-js@2';

type Body = {
  courseId: string;
  studentId: string;
  amount: number;
  currency?: string;
  successUrl?: string;
  cancelUrl?: string;
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

async function stripeRequest(path: string, form: URLSearchParams) {
  const response = await fetch(`https://api.stripe.com/v1/${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${stripeSecretKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: form.toString(),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.error?.message || 'Stripe request failed.');
  }
  return data;
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
    const amount = Number(body.amount || 0);
    const currency = (body.currency || 'usd').toLowerCase();

    if (!courseId || !studentId) {
      return jsonResponse({ error: 'courseId and studentId are required.' }, { status: 400 });
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      return jsonResponse({ error: 'amount must be greater than 0 for paid checkout.' }, { status: 400 });
    }

    const supabase = createClient(supabaseUrl, supabaseServiceRoleKey);
    const { data: course, error: courseError } = await supabase
      .from('courses')
      .select('id,title,price,pricingType,thumbnail')
      .eq('id', courseId)
      .single();

    if (courseError || !course) {
      return jsonResponse({ error: 'Course not found.' }, { status: 404 });
    }

    const successUrl =
      body.successUrl ||
      `${Deno.env.get('SITE_URL') || 'http://localhost:3000'}/student/course/${courseId}?payment=success&session_id={CHECKOUT_SESSION_ID}`;
    const cancelUrl =
      body.cancelUrl ||
      `${Deno.env.get('SITE_URL') || 'http://localhost:3000'}/student/course/${courseId}`;

    const session = await stripeRequest(
      'checkout/sessions',
      new URLSearchParams({
        mode: 'payment',
        success_url: successUrl,
        cancel_url: cancelUrl,
        'line_items[0][price_data][currency]': currency,
        'line_items[0][price_data][product_data][name]': course.title || 'Course enrollment',
        'line_items[0][price_data][product_data][description]': course.price
          ? `Enrollment for ${course.title}`
          : 'Course enrollment',
        'line_items[0][price_data][unit_amount]': Math.round(amount * 100).toString(),
        'line_items[0][quantity]': '1',
        'metadata[courseId]': courseId,
        'metadata[studentId]': studentId,
        'payment_intent_data[metadata][courseId]': courseId,
        'payment_intent_data[metadata][studentId]': studentId,
        'payment_intent_data[metadata][source]': 'skillshift_enrollment',
      })
    );

    return jsonResponse({
      sessionId: session.id,
      url: session.url,
      courseId,
      studentId,
    });
  } catch (error) {
    return jsonResponse(
      { error: error instanceof Error ? error.message : 'Unknown error' },
      { status: 500 }
    );
  }
}

Deno.serve(handler);
