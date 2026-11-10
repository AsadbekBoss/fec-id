import { redirect } from "next/navigation";

/** The app has no public landing page: go to the dashboard (which sends guests to /login). */
export default function Home() {
  redirect("/dashboard");
}
