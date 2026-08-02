import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { useAuth } from "../context/AuthContext";

export function RegisterPage() {
  const { register } = useAuth();
  const [employeeId, setEmployeeId] = useState("");
  const [realName, setRealName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirmPw) {
      setError("两次输入的密码不一致");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }
    setSubmitting(true);
    try {
      await register(employeeId.trim(), realName.trim(), password);
    } catch (err: any) {
      setError(err?.message?.replace(/^\d+\s+\w+:\s*/, "") || "注册失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center bg-background px-4 py-10 text-foreground">
      <Card className="w-full max-w-sm shadow-surface">
        <CardHeader className="pb-5">
          <div className="mb-3 h-1.5 w-12 rounded-full bg-primary" aria-hidden="true" />
          <CardTitle>注册 · 品成 BIM 知识库</CardTitle>
          <CardDescription>填写用户名、姓名和密码</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="register-employee-id" className="text-ui-sm font-medium text-foreground">
                用户名（登录用，唯一）
              </label>
              <Input
                id="register-employee-id"
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
              <label htmlFor="register-real-name" className="text-ui-sm font-medium text-foreground">
                真实姓名
              </label>
              <Input
                id="register-real-name"
                name="real-name"
                type="text"
                value={realName}
                onChange={(e) => setRealName(e.target.value)}
                autoComplete="name"
                required
                aria-invalid={Boolean(error)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="register-password" className="text-ui-sm font-medium text-foreground">
                密码（至少 6 位）
              </label>
              <Input
                id="register-password"
                name="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={6}
                aria-invalid={Boolean(error)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="register-password-confirmation" className="text-ui-sm font-medium text-foreground">
                确认密码
              </label>
              <Input
                id="register-password-confirmation"
                name="password-confirmation"
                type="password"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                autoComplete="new-password"
                required
                minLength={6}
                aria-invalid={Boolean(error)}
              />
            </div>
            {error && (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? "注册中…" : "注册"}
            </Button>
          </form>
          <div className="mt-5 text-center text-ui-sm text-muted-foreground">
            已有账号？{" "}
            <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
              登录
            </Link>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
