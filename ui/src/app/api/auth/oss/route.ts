/*
  Provides authentication token to LocalProviderWrapper once loaded
  in the browser.
  Returns 401 if no token cookie exists (user needs to log in).
*/
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

import { getAuthProvider } from '@/lib/auth/config';

const OSS_TOKEN_COOKIE = 'voice_console_auth_token';
const OSS_USER_COOKIE = 'voice_console_auth_user';

export async function GET() {
  const authProvider = await getAuthProvider();

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.json({ error: 'Not in OSS mode' }, { status: 400 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(OSS_TOKEN_COOKIE)?.value;
  const user = cookieStore.get(OSS_USER_COOKIE)?.value;

  // If no token exists, return 401 (user needs to sign up or log in)
  if (!token) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  let parsedUser: Record<string, unknown> = { id: token, name: 'Local User', provider: 'local', is_superuser: false };
  if (user) {
    try {
      const raw = user.startsWith('%') ? decodeURIComponent(user) : user;
      parsedUser = JSON.parse(raw);
    } catch {
      parsedUser = { id: token, name: 'Local User', provider: 'local', is_superuser: false };
    }
  }

  // Authoritative server-side verification: never trust client cookie for is_superuser
  try {
    const backendUrl = process.env.BACKEND_URL || 'http://localhost:8001';
    const authRes = await fetch(`${backendUrl}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
    if (authRes.ok) {
      const authData = await authRes.json();
      parsedUser.id = authData.id;
      parsedUser.email = authData.email;
      parsedUser.name = authData.name || parsedUser.name;
      parsedUser.is_superuser = Boolean(authData.is_superuser);
    } else {
      return NextResponse.json({ error: 'Invalid session' }, { status: 401 });
    }
  } catch {
    // If backend is unreachable, default is_superuser to false for safety
    parsedUser.is_superuser = false;
  }

  // Return the verified auth info as JSON
  return NextResponse.json({
    token,
    user: parsedUser,
  });
}
