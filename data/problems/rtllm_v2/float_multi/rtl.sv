module float_multi (
    input logic clk, rst,
    input logic [31:0] a, b,
    output logic [31:0] z
);
    function automatic [31:0] multiply_binary32(input [31:0] x, y);
        reg sign_bit;
        reg [7:0] ea,eb;
        reg [22:0] fa,fb;
        reg [23:0] ma,mb;
        reg [47:0] product;
        reg [48:0] rounded,remainder,halfway;
        integer xa,xb,leading,exponent,shift;
        begin
            sign_bit=x[31]^y[31]; ea=x[30:23]; eb=y[30:23];
            fa=x[22:0]; fb=y[22:0];
            if((ea==255 && fa!=0) || (eb==255 && fb!=0))
                multiply_binary32=32'hffc00000;
            else if((ea==255 && eb==0 && fb==0) || (eb==255 && ea==0 && fa==0))
                multiply_binary32=32'hffc00000;
            else if(ea==255 || eb==255) multiply_binary32={sign_bit,8'hff,23'd0};
            else if((ea==0 && fa==0) || (eb==0 && fb==0)) multiply_binary32={sign_bit,31'd0};
            else begin
                ma=ea==0 ? {1'b0,fa} : {1'b1,fa};
                mb=eb==0 ? {1'b0,fb} : {1'b1,fb};
                xa=ea==0 ? -126 : int'(ea)-127;
                xb=eb==0 ? -126 : int'(eb)-127;
                product=48'(ma)*48'(mb);
                leading=0;
                for(int i=0;i<48;i++) if(product[i]) leading=i;
                exponent=xa+xb-46+leading;
                shift=exponent>=-126 ? leading-23 : -(xa+xb+103);
                if(shift<=0) rounded={1'b0,product} << (-shift);
                else if(shift>48) rounded=0;
                else begin
                    rounded={1'b0,product} >> shift;
                    remainder={1'b0,product}-(rounded << shift);
                    halfway=49'd1 << (shift-1);
                    if(remainder>halfway || (remainder==halfway && rounded[0])) rounded=rounded+49'd1;
                end
                if(exponent < -126) multiply_binary32={sign_bit,7'd0,rounded[23:0]};
                else begin
                    if(rounded>=49'd16777216) begin rounded=rounded >> 1; exponent=exponent+1; end
                    if(exponent>127) multiply_binary32={sign_bit,8'hff,23'd0};
                    else multiply_binary32={sign_bit,8'(exponent+127),rounded[22:0]};
                end
            end
        end
    endfunction
    logic [2:0] phase;
    logic [31:0] pending_result;
    always_ff @(posedge clk or posedge rst) begin
        if(rst) begin phase<=0; pending_result<=0; z<=0; end
        else begin
            phase<=phase+3'd1;
            if(phase==0) pending_result<=multiply_binary32(a,b);
            if(phase==6) z<=pending_result;
        end
    end
endmodule
