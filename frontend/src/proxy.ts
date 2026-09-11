import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/middleware";

export default async function proxy(request: NextRequest) {
  return await updateSession(request);
}

export const config = {
  matcher: [
    /*
     * Every path except static assets and image files. The session has to be
     * refreshed on normal navigations; running this for a favicon request would
     * just be a wasted call to the Auth server.
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
