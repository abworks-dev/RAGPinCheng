import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { useAuth } from "../context/AuthContext";
import { AuthPageLayout } from "../components/AuthPageLayout";

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
    <AuthPageLayout>
      <Card className="w-full border-border/80 shadow-surface sm:p-3">
        <CardHeader className="pb-6">
          <p className="mb-2 text-ui-xs font-medium text-muted-foreground">加入知识协作</p>
          <CardTitle className="text-ui-xl tracking-tight">注册 · 品成 BIM 知识库</CardTitle>
          <CardDescription className="pt-2">填写用户名、姓名和密码，创建你的账号。</CardDescription>
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
    </AuthPageLayout>
  );
}
