-- handle_new_user() 트리거는 auth.users에 INSERT가 생기는 즉시(OTP 발송
-- 요청만으로도 발동) 실행되는데, 그 시점엔 username 메타데이터가 없어
-- "구글 가입"과 똑같이 이메일 앞부분으로 임시 아이디를 만들어 버린다.
-- 가입 완료(이메일 인증 뒤 "가입하기") 시점에, 폼에서 실제로 받은
-- 아이디·이름·전화번호·생년월일로 그 임시값을 덮어쓰는 함수.
-- 본인(auth.uid())의 프로필만 바꿀 수 있고, 로그인된 세션(authenticated)만
-- 호출할 수 있다.
create or replace function public.rpc_finalize_signup_profile(
    p_username text, p_name text, p_phone text, p_birthday date
)
returns boolean
language plpgsql security definer set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
begin
    if v_uid is null then
        return false;
    end if;
    if exists (select 1 from public.profiles where username = p_username and id <> v_uid) then
        return false;   -- 다른 사람이 이미 쓰는 아이디
    end if;
    update public.profiles
       set username = p_username, name = p_name, phone = p_phone, birthday = p_birthday
     where id = v_uid;
    return found;
end;
$$;

grant execute on function public.rpc_finalize_signup_profile(text, text, text, date) to authenticated;
