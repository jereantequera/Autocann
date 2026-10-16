# Product model

How Autocann models customers, tents, grows and crop types, and which part of
it is built where. This is the spec the implementation issues point to
(epic #34, crop profiles #32, customer journey #35, SaaS foundations #36).

Autocann is a SaaS IoT product: customers buy devices and use our cloud web
app; control runs on the device. `apps/tent` is our test rig, a single-tenant
instance of the same model.

## The model

```
Account (tenant, has a subscription plan)                 cloud only
 ├── Membership ── User, role: owner | grower | viewer     cloud only
 ├── FeedProduct (the account's nutrient catalog)
 ├── CropProfile (built-in, or a custom copy owned by the account)
 │    └── ProfileStage (ordered)
 └── Tent (a grow space)
      ├── Device(s) ── sensors (role) and outputs (kind)  cloud: #35
      └── Grow (at most one active per tent)
           ├── GrowStage (snapshot of the profile's stages, ordered)
           ├── StageEvent (when each stage started)
           ├── Irrigation ── IrrigationItem
           └── FeedPlan rows (dose per product, stage, week)
```

### Entities

**Account.** The tenant. Every other row belongs to exactly one account
(directly or through its parent). Holds the plan and its limits (#36). The
tent has no accounts: it is implicitly account 1.

**User / Membership.** A user can belong to several accounts; the membership
carries the role. `owner` manages billing, members and devices; `grower`
operates (stages, irrigations, profiles, outputs); `viewer` reads. Cloud only;
the tent keeps its single PIN (#12).

**Tent.** A grow space: name, timezone, and the devices in it. Sensors and
outputs belong to the tent's devices. The tent rig has exactly one tent,
seeded as "Carpa principal" and renamable.

**CropProfile.** What a crop needs, as data: `id`, `name`, `kind`
(`builtin` | `custom`), `account_id` (NULL for built-in), and its ordered
stages. Built-in profiles are read-only; customising one means copying it into
a `custom` profile.

**ProfileStage.** `key` (stable identifier, unique within the profile, e.g.
`early_veg`), `name` (display, Spanish), `position`, `expected_days` (NULL
means open-ended, allowed only on the last stage), a target that is **either**
a leaf-VPD band (`vpd_min`, `vpd_max`, kPa) **or** an air-humidity band
(`rh_min`, `rh_max`, %), and `water_only` (no nutrients that stage).
Validation matches `crates/protocol/src/profile.rs`: at least one stage,
`0 ≤ min < max ≤ 4 kPa` (or `0 ≤ min < max ≤ 100 %`), open stage only last.

**Grow.** One crop cycle in a tent: `tent_id`, `profile_id` (where it came
from), name, start/end dates, `current_stage` (a `GrowStage.key`), notes.
At most one active grow per tent.

**GrowStage.** A copy of the profile's stages taken when the grow starts.
This is the key design choice: **the control loop and the dashboard read the
active grow's stages, never the profile.** Editing or deleting a profile can
therefore never change what a running grow is doing; changing a running grow's
targets is an explicit edit of that grow. It also keeps history honest: a
finished grow still shows the bands it actually ran with.

**FeedProduct / FeedPlan.** The account's catalog, and doses per product,
stage key and week. The plan stays global in the tent for now (keys are
unchanged); scoping it to a grow, seeded from a profile's default plan, is a
later step.

## Where it lives

- **Tent (`apps/tent`):** SQLite, the backend's database. Tables
  `tents`, `crop_profiles`, `profile_stages`, `grow_stages`, plus `tent_id`
  and `profile_id` on `grows`. No accounts or users.
- **Cloud:** the cloud backend's database, same entities plus `accounts`,
  `users`, `memberships` and an `account_id` on every top-level row.
- **Devices:** receive the active grow's stages as a `CropProfile` message
  (`crates/protocol`), validate and store it, and control from it offline.
  The Rust crate defines the wire format, not the source of truth.

## Migration of the existing tent

Zero behaviour change, by construction:

1. Seed the built-in profile **"Cannabis fotoperiódico"** from today's
   constants in `apps/tent/autocann/control/vpd_math.py` and `web/app.py`,
   **with the same stage keys**: `seedling`, `early_veg`, `late_veg`,
   `flowering`, `dry` (`dry` uses the humidity band 60–65 %, `seedling` is
   `water_only`).
2. Seed tent 1, "Carpa principal".
3. Point every existing grow at tent 1 and that profile, and snapshot the
   profile's stages into `grow_stages` for each of them.
4. `grows.stage` values stay valid because the keys did not change.

## Build order

Each step is its own issue and leaves the system working:

1. Profiles in the database (tables, seed, read functions).
2. Tents, `grow_stages`, and grows linked to a tent and a profile (migration).
3. Dashboard and API read stages from the active grow.
4. The control loop reads its band from the active grow (`control`: reviewed
   by a person before merging).
5. Profiles API (list, read, copy, edit custom).
6. "Nuevo cultivo" picks a profile; tent name editable and shown.
7. Profile editor in the UI.
8. More built-in profiles (needs agronomic validation).

Accounts, users and roles come with the cloud backend (#36).
