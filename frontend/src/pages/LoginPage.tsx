import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const [employeeId, setEmployeeId] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(employeeId.trim(), password);
    } catch (err: any) {
      setError(err?.message?.replace(/^\d+\s+\w+:\s*/, "") || "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center bg-background px-4 py-10 text-foreground">
      <Card className="w-full max-w-sm shadow-surface">
        <CardHeader className="pb-5">
          <div className="mb-3 h-1.5 w-12 rounded-full bg-primary" aria-hidden="true" />
          <CardTitle>登录 · 品成 BIM 知识库</CardTitle>
          <CardDescription>使用用户名和密码登录</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="employee-id" className="text-ui-sm font-medium text-foreground">
                用户名
              </label>
              <Input
                id="employee-id"
                name="employee-id"
                type="text"
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
                autoComplete="username"
                autoFocus
                required
                aria-invalid={Boolean(error)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="password" className="text-ui-sm font-medium text-foreground">
                密码
              </label>
              <Input
                id="password"
                name="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                aria-invalid={Boolean(error)}
              />
            </div>
            {error && (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? "登录中…" : "登录"}
            </Button>
          </form>
          <div className="mt-5 text-center text-ui-sm text-muted-foreground">
            还没有账号？{" "}
            <Link to="/register" className="font-medium text-primary underline-offset-4 hover:underline">
              注册
            </Link>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
