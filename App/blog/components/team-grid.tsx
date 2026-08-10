'use client';

import Image from 'next/image';
import Link from 'next/link';
import { teamMembers, combinedEfforts, projectFlow } from '@/lib/team';

/**
 * Full team showcase for the "Meet the Team" post: member cards with real
 * photos and role chips, the combined-efforts panel, and the App project-flow
 * activity-ownership table. Driven entirely by lib/team.ts.
 */
export function TeamGrid() {
  return (
    <div className="space-y-12 mb-12 pb-12 border-b border-border">
      {/* Member cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {teamMembers.map((member) => (
          <div
            key={member.slug}
            className="flex flex-col items-center text-center p-6 rounded-xl border border-border bg-card/50 hover:bg-card transition-colors"
          >
            <Image
              src={member.photo}
              alt={`Portrait of ${member.name}`}
              width={112}
              height={112}
              className="w-28 h-28 rounded-full object-cover border border-border"
            />
            <h3 className="mt-4 text-lg font-semibold text-foreground">{member.name}</h3>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {member.roles.map((role) => (
                <span
                  key={role}
                  className="px-2.5 py-1 text-xs font-medium rounded-full text-accent bg-accent/10"
                >
                  {role}
                </span>
              ))}
            </div>
            <p className="mt-4 text-sm text-muted-foreground leading-relaxed">{member.blurb}</p>
            <Link
              href={`/blog/${member.slug}`}
              className="mt-4 text-sm font-semibold text-accent hover:text-accent/80 transition-colors"
            >
              Read deep dive →
            </Link>
          </div>
        ))}
      </div>

      {/* Combined efforts */}
      <div className="rounded-xl border border-border bg-secondary/30 p-6">
        <h3 className="text-xl font-bold text-foreground">Combined Efforts</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Tasks every member contributed to jointly.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {combinedEfforts.map((effort) => (
            <span
              key={effort}
              className="px-3 py-1.5 text-sm rounded-full bg-secondary text-foreground"
            >
              {effort}
            </span>
          ))}
        </div>
      </div>

      {/* Project-flow activity ownership */}
      <div>
        <h3 className="text-xl font-bold text-foreground">Project Flow — Activity Ownership</h3>
        <p className="mt-1 mb-4 text-sm text-muted-foreground">
          How each activity in the App project flow maps to its owner.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse border border-border">
            <thead>
              <tr className="bg-secondary">
                <th className="border border-border px-4 py-2 text-left font-semibold">Stage</th>
                <th className="border border-border px-4 py-2 text-left font-semibold">Activity</th>
                <th className="border border-border px-4 py-2 text-left font-semibold">Owner</th>
              </tr>
            </thead>
            <tbody>
              {projectFlow.map((row, i) => (
                <tr key={i} className="hover:bg-secondary/50">
                  <td className="border border-border px-4 py-2 text-muted-foreground">{row.stage}</td>
                  <td className="border border-border px-4 py-2">{row.activity}</td>
                  <td className="border border-border px-4 py-2 font-medium">{row.owner}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/**
 * Compact strip for the landing page: photo + name + lead role per member,
 * the whole block links through to the full Meet the Team post.
 */
export function TeamStrip() {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
      {teamMembers.map((member) => (
        <Link
          key={member.slug}
          href="/blog/meet-the-team"
          className="flex flex-col items-center text-center p-4 rounded-xl border border-border bg-card/50 hover:bg-card transition-colors"
        >
          <Image
            src={member.photo}
            alt={`Portrait of ${member.name}`}
            width={96}
            height={96}
            className="w-24 h-24 rounded-full object-cover border border-border"
          />
          <span className="mt-3 font-semibold text-foreground">{member.name}</span>
          <span className="mt-1 text-xs text-muted-foreground">{member.roles[0]}</span>
        </Link>
      ))}
    </div>
  );
}
