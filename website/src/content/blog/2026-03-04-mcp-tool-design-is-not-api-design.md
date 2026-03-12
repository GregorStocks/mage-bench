---
title: "MCP Tool Design Is Not API Design"
pubDate: 2026-03-04T08:00:00
author: Gregor
---

I've been working on [mage-bench](https://mage-bench.com) for over a month now, which I believe makes me an expert on MCP and agents.

A major piece of the mage-bench architecture is the Bridge, a headless game client that exposes MCP tools. Those tools allow my agents to understand the current game state and take actions.

I have strong opinions about API design. At first, I looked at MCP and said "ah, this is just an API, so I should just write a good API". I was pretty surprised when I started looking in the logs and saw how much trouble the LLMs were having with actually using the tools I'd built (LOOK AT YOUR LOGS). It turns out that a good MCP tool should have more in common with a CLI that you expect people to use rarely than with a production API.

In API design I love the pattern of making illegal states unrepresentable. This often entails doing things like this:

```protobuf
message ManaSource {
  oneof source {
    string object_id = 1;
    ManaInPoolIdentifier mana_in_pool = 2;
    AbilityIdentifier ability = 3;
    // ...
  }
}

message MyRequest {
  repeated ManaSource mana_plan = 1;
}
```

Complex nested structures, with all the possibilities encoded directly in the type system. If you do it right, this allows you to write very simple code on the client and on the server, with typechecking catching many possible errors at compile time, and small requests on the wire to boot. This is great for real software development. Catching errors at compile time is massively cheaper than catching them at runtime, and putting your constraints in the type system means tools like autocomplete can understand them.

But it's terrible for LLMs. LLMs are not writing software once to be reused over and over again - they are writing a single tool call by hand, which will be executed exactly once. And they don't have access to a compiler - they're just emitting JSON and hoping that it works. There's no meaningful "compile time vs runtime" distinction for them. So the benefits are smaller, and you're spending a lot of tokens on your fancy complicated format.

You also run into sillier problems like "the LLM really wants to emit a value for every possible field" which I expect to disappear eventually.

Empirically, a better pattern is this:

```protobuf
message MyRequest {
  // comma-separated list of ids, colors (to spend from pool),
  // or id:ability pairs. Example: "p1,p5,RED,RED,p3:2"
  string mana_plan = 1;
}
```

Just shove everything in a string and make the server return a meaningful error if it doesn't parse right. This is intellectually unsatisfying but empirically it works. This is a common pattern in developing with LLMs.
