module DivSqrtRecF64MulAddZ31 (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_sqrtOp,
    input logic [64:0] in_a, in_b,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [64:0] out_out,
    output logic [4:0] out_exceptionFlags
);
function automatic [64:0] quantize(input logic [55:0] sig,input integer shift,input logic [2:0] mode,input logic signbit);
    logic [63:0] q,r,h;logic inc;
    begin
        q=0;r=sig;h=64'd1<<56;
        if(shift<=56) begin q=sig>>shift;r=sig&((64'd1<<shift)-1);h=64'd1<<(shift-1);end
        inc=0;
        case(mode)
            0:inc=(r>h)||((r==h)&&q[0]);
            4:inc=r>=h;
            2:inc=signbit&&(r!=0);
            3:inc=!signbit&&(r!=0);
            default:inc=0;
        endcase
        if(inc)q=q+1;
        if(mode==6 && r!=0)q=q|1;
        quantize={r!=0,q};
    end
endfunction
function automatic [69:0] pack_rounded(input logic [55:0] sig,input integer exponent,input logic [2:0] mode,input logic signbit,input logic dt);
    logic [64:0] result,qr;logic [63:0] q;logic [4:0] flags;
    integer e,ue,shift,top,j;logic tiny;
    begin
        result={signbit,64'b0};flags=0;e=exponent-2048;
        qr=quantize(sig,2,mode,signbit);ue=e+(qr[63:0]>=(64'd1<<53));
        if(ue>1023)begin
            flags=5;
            if(mode==0 || mode==4 || (mode==2 && signbit) || (mode==3 && !signbit))result=result|65'hc000000000000000;
            else result=result|65'hbfffffffffffffff;
        end else begin
            shift=(e < -1022) ? 1028-exponent : 2;
            qr=quantize(sig,shift,mode,signbit);q=qr[63:0];tiny=dt ? ue < -1022 : e < -1022;
            if(qr[64])flags=tiny ? 3 : 1;
            if(e>=-1022)begin
                if(q>=(64'd1<<53))begin q=q>>1;e=e+1;end
                result=result|(65'(e+2048)<<52)|(q&52'hfffffffffffff);
            end else if(q!=0)begin
                top=0;for(j=0;j<53;j=j+1)if(q>=(64'd1<<j))top=j;
                result=result|(65'(top+974)<<52)|((q<<(52-top))&52'hfffffffffffff);
            end
        end
        pack_rounded={flags,result};
    end
endfunction

logic [64:0] result;
logic [4:0] flags;
logic [2:0] ta,tb;
logic na,nb,sna,snb,invalid,s;
logic [52:0] a,b;
logic [111:0] wide,root,trial,q;
logic [69:0] packed_result;
integer e,exponent,shift,i;
always_comb begin
    ta=in_a[63:61];tb=in_b[63:61];na=ta==7;nb=tb==7;
    sna=na&&!in_a[51];snb=nb&&!in_b[51];s=in_a[64]^in_b[64];
    result={s,64'b0};flags=0;invalid=0;a=0;b=0;wide=0;root=0;trial=0;q=0;
    packed_result=0;e=0;exponent=0;shift=0;
    if(in_sqrtOp)begin
        result={in_b[64],64'b0};
        if(nb || (in_b[64] && tb!=0))begin
            result=65'he008000000000000;
            if(snb || (!nb && in_b[64] && tb!=0))flags=16;
        end else if(tb==6)result=65'hc000000000000000;
        else if(tb!=0)begin
            e=int'(in_b[63:52])-2048;exponent=(e>>>1)+2048;
            wide={1'b1,in_b[51:0]};wide=wide<<(56+(e&1));
            for(i=54;i>=0;i=i-1)begin
                trial=root|(112'd1<<i);
                if(trial*trial<=wide)root=trial;
            end
            if(root*root!=wide)root=root|1;
            packed_result=pack_rounded(root[55:0],exponent,in_roundingMode,0,in_detectTininess);
            result=packed_result[64:0];flags=packed_result[69:65];
        end
    end else begin
        invalid=sna||snb||(ta==0 && tb==0)||(ta==6 && tb==6);
        if(na||nb||invalid)begin result=65'he008000000000000;if(invalid)flags=16;end
        else if(ta==6 || tb==0)begin result=result|65'hc000000000000000;if(ta!=6 && tb==0)flags=8;end
        else if(ta!=0 && tb!=6)begin
            a={1'b1,in_a[51:0]};b={1'b1,in_b[51:0]};
            exponent=int'(in_a[63:52])-int'(in_b[63:52])+2048;shift=54;
            if(a<b)begin shift=55;exponent=exponent-1;end
            wide=a;wide=wide<<shift;q=wide/b;if(wide%b!=0)q=q|1;
            packed_result=pack_rounded(q[55:0],exponent,in_roundingMode,s,in_detectTininess);
            result=packed_result[64:0];flags=packed_result[69:65];
        end
    end
end
assign in_ready=!out_valid || out_ready;
always_ff @(posedge clk)begin
    if(rst)begin out_valid<=0;out_out<=0;out_exceptionFlags<=0;end
    else if(in_ready)begin out_valid<=in_valid;out_out<=in_valid ? result : 0;out_exceptionFlags<=in_valid ? flags : 0;end
end
endmodule
