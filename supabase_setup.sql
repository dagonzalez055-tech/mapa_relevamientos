-- Preparación de Supabase para el mapa de relevamientos.
-- Se pega UNA vez en Supabase > SQL Editor > New query > Run. Se puede volver a correr sin problema.

-- Archivos cargados a mano (el contenido va en Storage; esta tabla dice cuál es la versión vigente).
create table if not exists public.archivos (
    nombre      text primary key,
    sha256      text not null,
    bytes       bigint not null default 0,
    version     integer not null default 1,
    actualizado timestamptz not null default now()
);

-- Registro de cambios (quién hizo qué).
create table if not exists public.cambios (
    id      bigserial primary key,
    fecha   timestamptz not null default now(),
    usuario text not null,
    accion  text not null,
    detalle text not null default ''
);
create index if not exists cambios_fecha_idx on public.cambios (fecha desc);

-- Usuarios con permiso para entrar. Roles: lector, editor, admin.
create table if not exists public.usuarios (
    email  text primary key,
    nombre text not null default '',
    rol    text not null default 'lector' check (rol in ('lector', 'editor', 'admin')),
    activo boolean not null default true,
    creado timestamptz not null default now()
);

-- Marca de actividad: el aviso diario anota acá cuándo se hizo.
create table if not exists public.actividad (
    clave text primary key,
    valor timestamptz not null default now()
);

-- Seguridad: con RLS activado y sin políticas, nadie puede leer ni escribir con la clave pública.
-- El programa usa la clave secreta (service role), que no pasa por RLS y nunca sale del servidor.
alter table public.archivos  enable row level security;
alter table public.cambios   enable row level security;
alter table public.usuarios  enable row level security;
alter table public.actividad enable row level security;

-- Carpeta privada de archivos.
insert into storage.buckets (id, name, public)
values ('datos', 'datos', false)
on conflict (id) do nothing;

-- Funciones auxiliares (solo las puede ejecutar la clave secreta).
create or replace function public.tamano_base() returns bigint
language sql security definer set search_path = public as
$$ select pg_database_size(current_database()) $$;

create or replace function public.tamano_archivos() returns bigint
language sql security definer set search_path = public, storage as
$$ select coalesce(sum((metadata->>'size')::bigint), 0) from storage.objects where bucket_id = 'datos' $$;

-- Aviso diario: hace una consulta real y anota la fecha. Evita que Supabase pause el proyecto por inactividad.
create or replace function public.ping() returns timestamptz
language plpgsql security definer set search_path = public as
$$
begin
    insert into public.actividad (clave, valor) values ('ping', now())
    on conflict (clave) do update set valor = excluded.valor;
    return now();
end
$$;

revoke all on function public.tamano_base()     from public, anon, authenticated;
revoke all on function public.tamano_archivos() from public, anon, authenticated;
revoke all on function public.ping()            from public, anon, authenticated;
grant execute on function public.tamano_base()     to service_role;
grant execute on function public.tamano_archivos() to service_role;
grant execute on function public.ping()            to service_role;
