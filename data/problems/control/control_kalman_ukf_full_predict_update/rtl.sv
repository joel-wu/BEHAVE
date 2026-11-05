module control_kalman_ukf_full_predict_update (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_measurement,
    input logic [15:0] in_dt,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_x,
    output logic [95:0] out_p,
    output logic [63:0] out_gain,
    output logic [63:0] out_innovation,
    output logic [31:0] out_variance,
    output logic out_error
);
    // 0: idle, 1: prior-state roots, 2: prediction, 3: new-prior roots, 4: update.
    logic [2:0] phase;
    logic [5:0] count;
    logic [79:0] radicand [0:2], root_work [0:2], bit_work;
    logic offdiag_negative;
    logic signed [31:0] state_x0, state_x1, state_p00, state_p01, state_p11;
    logic signed [31:0] prior_x0, prior_x1, prior_p00, prior_p01, prior_p11;
    logic signed [31:0] measurement;
    logic [15:0] delta;
    logic signed [127:0] p0, p1, p2, determinant, n00, n01, n11;
    logic signed [127:0] m0, m1, u00, u01, u11;
    logic signed [127:0] a [0:4], b [0:4], f0 [0:4], f1 [0:4];
    logic signed [127:0] sum0, sum1, xp0, xp1, dx0, dx1, dz, w4, w;
    logic signed [127:0] c00, c01, c11, pp00, pp01, pp11;
    logic signed [127:0] zp, ss, cs0, cs1, variance, pxz0, pxz1, k0, k1, innovation;
    logic signed [127:0] nx0, nx1, np00, np01, np11;
    logic prediction_ok, update_ok;

    function automatic signed [127:0] rne(input signed [127:0] n, input signed [127:0] d);
        reg signed [127:0] magnitude, q, r;
        begin
            magnitude = n < 0 ? -n : n;
            q = magnitude/d; r = magnitude%d;
            if (2*r > d || (2*r == d && q[0])) q=q+1;
            rne = n < 0 ? -q : q;
        end
    endfunction

    function automatic in32(input signed [127:0] v);
        in32 = v>= -128'sd2147483648 && v<=128'sd2147483647;
    endfunction

    function automatic valid_state(input signed [127:0] x0, x1, p00, p01, p11);
        valid_state = in32(x0) && in32(x1) && in32(p00) && in32(p01) && in32(p11)
                      && p00>0 && p11>0 && p00*p11-p01*p01>0;
    endfunction

    always_comb begin
        p0=state_p00; p1=state_p01; p2=state_p11;
        m0=state_x0; m1=state_x1;
        if(phase==4) begin
            m0=prior_x0; m1=prior_x1;
        end
        m0=m0<<<16; m1=m1<<<16;
        u00=$signed({1'b0,root_work[0]});
        u01=$signed({1'b0,root_work[1]});
        u11=$signed({1'b0,root_work[2]});
        if(offdiag_negative) u01=-u01;
        for(integer j=0;j<5;j=j+1)begin a[j]=0;b[j]=0;f0[j]=0;f1[j]=0;end
        sum0=0;sum1=0;xp0=0;xp1=0;dx0=0;dx1=0;dz=0;w4=0;w=0;
        c00=0;c01=0;c11=0;pp00=0;pp01=0;pp11=0;
        zp=0;ss=0;cs0=0;cs1=0;variance=0;pxz0=0;pxz1=0;
        k0=0;k1=0;innovation=0;nx0=0;nx1=0;np00=0;np01=0;np11=0;
        prediction_ok=0;update_ok=0;
        if(phase==2 || phase==4) begin
            a[0]=m0; b[0]=m1;
            a[1]=m0+u00; b[1]=m1+u01;
            a[2]=m0; b[2]=m1+u11;
            a[3]=m0-u00; b[3]=m1-u01;
            a[4]=m0; b[4]=m1-u11;
            if(phase==2) begin
                for(integer j=0;j<5;j=j+1) begin
                    f0[j]=a[j]+rne($signed({1'b0,delta})*b[j],128'sd65536);
                    f1[j]=b[j];w=(j==0)?-3:1;
                    sum0=sum0+w*f0[j];sum1=sum1+w*f1[j];
                end
                xp0=rne(sum0,128'sd65536);xp1=rne(sum1,128'sd65536);
                for(integer j=0;j<5;j=j+1) begin
                    dx0=f0[j]-(xp0<<<16);dx1=f1[j]-(xp1<<<16);w4=(j==0)?-1:4;
                    c00=c00+w4*dx0*dx0;c01=c01+w4*dx0*dx1;c11=c11+w4*dx1*dx1;
                end
                pp00=rne(c00,128'sd1125899906842624)+512;
                pp01=rne(c01,128'sd1125899906842624);
                pp11=rne(c11,128'sd1125899906842624)+512;
                prediction_ok=valid_state(xp0,xp1,pp00,pp01,pp11);
                if(prediction_ok)begin p0=pp00;p1=pp01;p2=pp11;end
            end else begin
                for(integer j=0;j<5;j=j+1)begin w=(j==0)?-3:1;sum0=sum0+w*a[j];end
                zp=rne(sum0,128'sd65536);
                for(integer j=0;j<5;j=j+1) begin
                    dx0=a[j]-m0;dx1=b[j]-m1;dz=a[j]-(zp<<<16);w4=(j==0)?-1:4;
                    ss=ss+w4*dz*dz;cs0=cs0+w4*dx0*dz;cs1=cs1+w4*dx1*dz;
                end
                variance=rne(ss,128'sd1125899906842624)+512;
                pxz0=rne(cs0,128'sd1125899906842624);pxz1=rne(cs1,128'sd1125899906842624);
                if(variance>0 && variance<=128'sd2147483647) begin
                    k0=rne(pxz0<<<16,variance);k1=rne(pxz1<<<16,variance);
                    innovation=$signed(measurement)-zp;
                    nx0=prior_x0;nx1=prior_x1;
                    nx0=rne((nx0<<<16)+k0*innovation,128'sd65536);
                    nx1=rne((nx1<<<16)+k1*innovation,128'sd65536);
                    np00=prior_p00;np01=prior_p01;np11=prior_p11;
                    np00=rne((np00<<<32)-k0*k0*variance,128'sd4294967296);
                    np01=rne((np01<<<32)-k0*k1*variance,128'sd4294967296);
                    np11=rne((np11<<<32)-k1*k1*variance,128'sd4294967296);
                    update_ok=valid_state(nx0,nx1,np00,np01,np11) && in32(k0) && in32(k1);
                end
            end
        end
        determinant=p0*p2-p1*p1;
        n00=0;n01=0;n11=0;
        if(p0>0 && determinant>0)begin
            n00=p0<<<47;
            n01=(p1*p1<<<47)/p0;
            n11=(determinant<<<47)/p0;
        end
    end

    assign in_ready=!rst && phase==0 && (!out_valid || out_ready);
    always_ff @(posedge clk)begin
        if(rst)begin
            phase<=0;count<=0;bit_work<=0;offdiag_negative<=0;measurement<=0;delta<=0;
            state_x0<=0;state_x1<=0;state_p00<=32768;state_p01<=0;state_p11<=32768;
            prior_x0<=0;prior_x1<=0;prior_p00<=0;prior_p01<=0;prior_p11<=0;
            for(integer j=0;j<3;j=j+1)begin radicand[j]<=0;root_work[j]<=0;end
            out_valid<=0;out_x<=0;out_p<=0;out_gain<=0;out_innovation<=0;out_variance<=0;out_error<=0;
        end else begin
            if(out_valid && out_ready)out_valid<=0;
            if(in_valid && in_ready)begin
                measurement<=in_measurement;delta<=in_dt;phase<=1;count<=0;bit_work<=80'd1<<78;
                offdiag_negative<=state_p01[31];
                radicand[0]<=n00;radicand[1]<=n01;radicand[2]<=n11;
                for(integer j=0;j<3;j=j+1)root_work[j]<=0;
            end else if(phase==1 || phase==3)begin
                for(integer j=0;j<3;j=j+1)begin
                    if(radicand[j]>=root_work[j]+bit_work)begin
                        radicand[j]<=radicand[j]-root_work[j]-bit_work;
                        root_work[j]<=(root_work[j]>>1)+bit_work;
                    end else root_work[j]<=root_work[j]>>1;
                end
                bit_work<=bit_work>>2;
                if(count==39)begin phase<=phase+1;count<=0;end
                else count<=count+1;
            end else if(phase==2)begin
                if(prediction_ok)begin
                    prior_x0<=xp0;prior_x1<=xp1;prior_p00<=pp00;prior_p01<=pp01;prior_p11<=pp11;
                    radicand[0]<=n00;radicand[1]<=n01;radicand[2]<=n11;
                    for(integer j=0;j<3;j=j+1)root_work[j]<=0;
                    bit_work<=80'd1<<78;offdiag_negative<=pp01<0;phase<=3;
                end else begin
                    phase<=0;out_valid<=1;out_error<=1;
                    out_x<=0;out_p<=0;out_gain<=0;out_innovation<=0;out_variance<=0;
                end
            end else if(phase==4)begin
                phase<=0;out_valid<=1;out_error<=!update_ok;
                out_x<=0;out_p<=0;out_gain<=0;out_innovation<=0;out_variance<=0;
                if(update_ok)begin
                    state_x0<=nx0;state_x1<=nx1;state_p00<=np00;state_p01<=np01;state_p11<=np11;
                    out_x<={nx1[31:0],nx0[31:0]};out_p<={np11[31:0],np01[31:0],np00[31:0]};
                    out_gain<={k1[31:0],k0[31:0]};out_innovation<=innovation;out_variance<=variance;
                end
            end
        end
    end
endmodule
