module banded_lu_factor4x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_ab_0_0,
    input  logic [63:0] in_ab_0_1,
    input  logic [63:0] in_ab_0_2,
    input  logic [63:0] in_ab_0_3,
    input  logic [63:0] in_ab_1_0,
    input  logic [63:0] in_ab_1_1,
    input  logic [63:0] in_ab_1_2,
    input  logic [63:0] in_ab_1_3,
    input  logic [63:0] in_ab_2_0,
    input  logic [63:0] in_ab_2_1,
    input  logic [63:0] in_ab_2_2,
    input  logic [63:0] in_ab_2_3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_ab_0_0,
    output logic [63:0] out_ab_0_1,
    output logic [63:0] out_ab_0_2,
    output logic [63:0] out_ab_0_3,
    output logic [63:0] out_ab_1_0,
    output logic [63:0] out_ab_1_1,
    output logic [63:0] out_ab_1_2,
    output logic [63:0] out_ab_1_3,
    output logic [63:0] out_ab_2_0,
    output logic [63:0] out_ab_2_1,
    output logic [63:0] out_ab_2_2,
    output logic [63:0] out_ab_2_3,
    output logic [63:0] out_ab_3_0,
    output logic [63:0] out_ab_3_1,
    output logic [63:0] out_ab_3_2,
    output logic [63:0] out_ab_3_3,
    output logic [2:0]  out_ipiv_0,
    output logic [2:0]  out_ipiv_1,
    output logic [2:0]  out_ipiv_2,
    output logic [2:0]  out_ipiv_3,
    output logic [2:0]  out_info
);
function automatic logic signed [63:0] qm(input logic signed [63:0] x,y);
    logic signed [127:0] n;n=x;n=n*y;qm=n >>> 32;
endfunction
function automatic logic signed [63:0] qd(input logic signed [63:0] x,y);
    logic signed [127:0] n,d;n=x;n=n <<< 32;d=y;qd=y==0?64'sd0:n/d;
endfunction
function automatic logic [63:0] mag(input logic signed [63:0] x);
    mag=x<0?(~$unsigned(x))+64'd1:$unsigned(x);
endfunction
logic signed [63:0] a[0:3][0:3],tmp;
integer piv[0:3],info,ju,p,candidate;
assign in_ready=!out_valid || out_ready;
always @(posedge clk) begin
    if(rst) begin
        out_valid<=0;
        out_ab_0_0<=0;
        out_ab_0_1<=0;
        out_ab_0_2<=0;
        out_ab_0_3<=0;
        out_ab_1_0<=0;
        out_ab_1_1<=0;
        out_ab_1_2<=0;
        out_ab_1_3<=0;
        out_ab_2_0<=0;
        out_ab_2_1<=0;
        out_ab_2_2<=0;
        out_ab_2_3<=0;
        out_ab_3_0<=0;
        out_ab_3_1<=0;
        out_ab_3_2<=0;
        out_ab_3_3<=0;
        out_ipiv_0<=0;
        out_ipiv_1<=0;
        out_ipiv_2<=0;
        out_ipiv_3<=0;
        out_info<=0;
    end else if(in_ready) begin
        out_valid<=in_valid;
        if(in_valid) begin
            for(integer r=0;r<4;r=r+1) for(integer c=0;c<4;c=c+1) a[r][c]=0;
            a[0][0]=in_ab_1_0;
            a[1][0]=in_ab_2_0;
            a[1][1]=in_ab_1_1;
            a[0][1]=in_ab_0_1;
            a[2][1]=in_ab_2_1;
            a[2][2]=in_ab_1_2;
            a[1][2]=in_ab_0_2;
            a[3][2]=in_ab_2_2;
            a[3][3]=in_ab_1_3;
            a[2][3]=in_ab_0_3;
            info=0;ju=0;
            for(integer j=0;j<4;j=j+1) begin
                p=j;if(j<3 && mag(a[j+1][j])>mag(a[j][j])) p=j+1;
                piv[j]=p+1;
                if(a[p][j]==0) begin if(info==0) info=j+1;end
                else begin
                    candidate=(p+1>3)?3:p+1;if(candidate>ju)ju=candidate;
                    for(integer c=0;c<4;c=c+1) begin
                        if(j<=c && c<=ju) begin
                            tmp=a[j][c];a[j][c]=a[p][c];a[p][c]=tmp;
                        end
                    end
                    if(j<3) begin
                        a[j+1][j]=qd(a[j+1][j],a[j][j]);
                        for(integer c=0;c<4;c=c+1)
                            if(j<c && c<=ju)
                                a[j+1][c]=a[j+1][c]-qm(a[j+1][j],a[j][c]);
                    end
                end
            end
            out_ab_0_0<=0;
            out_ab_0_1<=0;
            out_ab_0_2<=a[0][2];
            out_ab_0_3<=a[1][3];
            out_ab_1_0<=0;
            out_ab_1_1<=a[0][1];
            out_ab_1_2<=a[1][2];
            out_ab_1_3<=a[2][3];
            out_ab_2_0<=a[0][0];
            out_ab_2_1<=a[1][1];
            out_ab_2_2<=a[2][2];
            out_ab_2_3<=a[3][3];
            out_ab_3_0<=a[1][0];
            out_ab_3_1<=a[2][1];
            out_ab_3_2<=a[3][2];
            out_ab_3_3<=0;
            out_ipiv_0<=piv[0];
            out_ipiv_1<=piv[1];
            out_ipiv_2<=piv[2];
            out_ipiv_3<=piv[3];
            out_info<=info;
        end
    end
end
endmodule
