"use client";

import { useCallback, useEffect, useState } from "react";
import { clearToken, me, type User } from "@/lib/api";
import Login from "@/components/Login";
import Workbench from "@/components/Workbench";

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const onLogin = useCallback((u: User) => setUser(u), []);

  const onLogout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "#6b7280" }}>
        加载中…
      </div>
    );
  }

  if (!user) {
    return <Login onLogin={onLogin} />;
  }

  return <Workbench user={user} onLogout={onLogout} />;
}
