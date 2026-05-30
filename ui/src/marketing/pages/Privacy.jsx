// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Privacy page. Translated from pages/privacy.body.html per
 * §Translation rules. Prose privacy policy.
 */
export default function Privacy() {
  return (
    <SiteLayout>
      <section className="page-head wrap" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Legal</div>
        <h1>Privacy Policy</h1>
      </section>

      <section className="section wrap" style={{ paddingTop: "28px" }}>
        <div className="legal-meta">Last updated: May 28, 2026</div>
        <div className="prose">
          <p>
            This policy explains what Channel Labs collects, how we use it, and the choices you have. We&apos;ve tried to
            write it in plain language. It&apos;s a product summary, not legal advice.
          </p>

          <h2>What we collect</h2>
          <p>
            When you sign in with Google, we receive your name, email address, and profile image to create and secure
            your account. We do not receive your Google password.
          </p>
          <p>
            To run the product, we store the content you create in Channel — your chats, projects, uploaded files, and
            artifacts — along with basic settings like your chosen theme and default model.
          </p>
          <p>
            We collect limited diagnostic data (app version, crash reports, and aggregate usage events) to keep Channel
            working and improve it. On the desktop app, this is local-first where possible.
          </p>

          <h2>How we use it</h2>
          <ul>
            <li>To provide the service — sending your prompts to the model you select and returning responses.</li>
            <li>To sync your work across the web and desktop apps.</li>
            <li>To secure accounts, prevent abuse, and meet legal obligations.</li>
            <li>To diagnose problems and improve features.</li>
          </ul>

          <h2>Your data and model training</h2>
          <p>
            We do not use your conversations, files, or artifacts to train models. Prompts are sent to our model
            provider only to generate your responses. Team plans include a contractual no-training guarantee.
          </p>

          <h2>Sharing</h2>
          <p>
            We share data only with service providers who help us operate Channel (such as our cloud host and model
            provider), under agreements that limit their use to providing those services. We do not sell your personal
            information.
          </p>

          <h2>Your rights</h2>
          <p>
            You can export or delete your data at any time from Customize, or by contacting us. Deleting your account
            removes your content from our active systems, subject to limited backups and legal retention.
          </p>

          <h2>Contact</h2>
          <p>
            Questions about this policy? Reach us at <a href="#">privacy@channel.app</a>.
          </p>
        </div>
      </section>
    </SiteLayout>
  );
}
