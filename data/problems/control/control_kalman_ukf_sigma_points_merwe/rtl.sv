module MerweSigmaPoints (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [31:0] in_x0, in_x1,
    input logic signed [31:0] in_p00, in_p01, in_p10, in_p11,
    input logic [15:0] in_alpha,
    input logic signed [15:0] in_beta, in_kappa,
    output logic out_valid,
    input logic out_ready,
    output logic signed [63:0] out_sigma0_0,
    output logic signed [63:0] out_sigma0_1,
    output logic signed [63:0] out_sigma1_0,
    output logic signed [63:0] out_sigma1_1,
    output logic signed [63:0] out_sigma2_0,
    output logic signed [63:0] out_sigma2_1,
    output logic signed [63:0] out_sigma3_0,
    output logic signed [63:0] out_sigma3_1,
    output logic signed [63:0] out_sigma4_0,
    output logic signed [63:0] out_sigma4_1,
    output logic signed [63:0] out_wm0,
    output logic signed [63:0] out_wm1,
    output logic signed [63:0] out_wm2,
    output logic signed [63:0] out_wm3,
    output logic signed [63:0] out_wm4,
    output logic signed [63:0] out_wc0,
    output logic signed [63:0] out_wc1,
    output logic signed [63:0] out_wc2,
    output logic signed [63:0] out_wc3,
    output logic signed [63:0] out_wc4,
    output logic out_error
);
    logic busy;
    logic [5:0] count;
    logic [83:0] radicand [0:2];
    logic [83:0] root_work [0:2];
    logic [83:0] bit_work;
    logic signed [63:0] mean0, mean1, wm0, wc0, other;
    logic offdiag_negative;
    logic signed [63:0] u00, u01, u11;
    logic signed [127:0] a, beta, kappa, p00, p01, p10, p11, d, det;
    logic signed [127:0] n00, n01, n11, w0, c0, co;
    logic invalid;

    always_comb begin
        a = {1'b0, in_alpha}; beta = in_beta; kappa = in_kappa;
        p00 = in_p00; p01 = in_p01; p10 = in_p10; p11 = in_p11;
        det = p00*p11 - p01*p01;
        invalid = a==0 || kappa< -128'sd256 || kappa>128'sd1024 ||
                  beta<0 || beta>128'sd1024 || p00<=0 || p11<=0 || p01!=p10 || det<=0;
        d=0; n00=0; n01=0; n11=0; w0=0; c0=0; co=0;
        if(!invalid) begin
            d = a*a*(128'sd512+kappa);
            n00 = d*p00*128'sd256;
            n01 = (d*p01*p01*128'sd256)/p00;
            n11 = (d*det*128'sd256)/p00;
            w0 = ((d-128'sd2199023255552)*128'sd16777216)/d;
            co = 128'sd9223372036854775808/d;
            c0 = ((2*d-128'sd2199023255552)*128'sd4294967296 - d*a*a + d*beta*128'sd16777216)/(d*128'sd256);
        end
        u00 = $signed({1'b0, root_work[0][42:0]});
        u01 = $signed({1'b0, root_work[1][42:0]});
        if(offdiag_negative) u01 = -u01;
        u11 = $signed({1'b0, root_work[2][42:0]});
    end

    assign in_ready = !rst && !busy && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if(rst) begin
            busy<=0;count<=0;bit_work<=0;out_valid<=0;out_error<=0;
            mean0<=0;mean1<=0;wm0<=0;wc0<=0;other<=0;offdiag_negative<=0;
            for(integer j=0;j<3;j=j+1)begin radicand[j]<=0;root_work[j]<=0;end
            out_sigma0_0<=0;
            out_sigma0_1<=0;
            out_sigma1_0<=0;
            out_sigma1_1<=0;
            out_sigma2_0<=0;
            out_sigma2_1<=0;
            out_sigma3_0<=0;
            out_sigma3_1<=0;
            out_sigma4_0<=0;
            out_sigma4_1<=0;
            out_wm0<=0;
            out_wm1<=0;
            out_wm2<=0;
            out_wm3<=0;
            out_wm4<=0;
            out_wc0<=0;
            out_wc1<=0;
            out_wc2<=0;
            out_wc3<=0;
            out_wc4<=0;
        end else begin
            if(out_valid && out_ready)out_valid<=0;
            if(in_valid && in_ready) begin
                out_error<=invalid;
                if(invalid) begin
                    out_valid<=1;
                    out_sigma0_0<=0;
                    out_sigma0_1<=0;
                    out_sigma1_0<=0;
                    out_sigma1_1<=0;
                    out_sigma2_0<=0;
                    out_sigma2_1<=0;
                    out_sigma3_0<=0;
                    out_sigma3_1<=0;
                    out_sigma4_0<=0;
                    out_sigma4_1<=0;
                    out_wm0<=0;
                    out_wm1<=0;
                    out_wm2<=0;
                    out_wm3<=0;
                    out_wm4<=0;
                    out_wc0<=0;
                    out_wc1<=0;
                    out_wc2<=0;
                    out_wc3<=0;
                    out_wc4<=0;
                end else begin
                    busy<=1;count<=0;bit_work<=84'd1<<82;
                    mean0<={{16{in_x0[31]}},in_x0,16'd0};
                    mean1<={{16{in_x1[31]}},in_x1,16'd0};
                    wm0<=w0;wc0<=c0;other<=co;offdiag_negative<=in_p01[31];
                    radicand[0]<=n00;radicand[1]<=n01;radicand[2]<=n11;
                    for(integer j=0;j<3;j=j+1)root_work[j]<=0;
                end
            end else if(busy) begin
                if(count<42) begin
                    for(integer j=0;j<3;j=j+1) begin
                        if(radicand[j]>=root_work[j]+bit_work) begin
                            radicand[j]<=radicand[j]-root_work[j]-bit_work;
                            root_work[j]<=(root_work[j]>>1)+bit_work;
                        end else root_work[j]<=root_work[j]>>1;
                    end
                    bit_work<=bit_work>>2;count<=count+1;
                end else begin
                    busy<=0;out_valid<=1;
                    out_sigma0_0<=mean0;
                    out_sigma0_1<=mean1;
                    out_sigma1_0<=mean0+u00;
                    out_sigma1_1<=mean1+u01;
                    out_sigma2_0<=mean0;
                    out_sigma2_1<=mean1+u11;
                    out_sigma3_0<=mean0-u00;
                    out_sigma3_1<=mean1-u01;
                    out_sigma4_0<=mean0;
                    out_sigma4_1<=mean1-u11;
                    out_wm0<=wm0;
                    out_wm1<=other;
                    out_wm2<=other;
                    out_wm3<=other;
                    out_wm4<=other;
                    out_wc0<=wc0;
                    out_wc1<=other;
                    out_wc2<=other;
                    out_wc3<=other;
                    out_wc4<=other;
                end
            end
        end
    end
endmodule
